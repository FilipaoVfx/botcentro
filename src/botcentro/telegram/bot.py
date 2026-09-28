"""Servicio del bot por sondeo largo (DEC-16; SRS §11).

Ciclo: getUpdates → `telegram_accept_update` (seudonimiza, deduplica, autoriza y encola) →
trabajador de la cola: `query_begin` → motor sin IA → `query_finish` (respuesta y segmentos) →
envío de cada segmento y `delivery_mark`. Un fallo de envío no repite la consulta: los segmentos
ya enviados no se reenvían (SRS-F23).
"""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any

from pydantic import ValidationError

from botcentro.errors import FailureKind
from botcentro.insforge.client import RpcClient, as_rows
from botcentro.jobs.queue import Job, JobQueue
from botcentro.query.engine import Answer, AnswerEngine, plain_text
from botcentro.domain.enums import QueryIntent
from botcentro.telegram.client import BotApi, TelegramApiError
from botcentro.telegram.render import Section, render_sections, split_message
from botcentro.telegram.webhook import TelegramWebhook, TgUpdate

log = logging.getLogger(__name__)
KINDS = ("query.telegram_message", "query.telegram_callback")
COMMANDS = (("help", "Qué puedo responder y ejemplos"), ("proyecto", "Ficha de un proyecto: /proyecto 178 de 2025 Senado"),
            ("agenda", "Agenda publicada de esta semana"), ("fuentes", "De dónde salen los datos"),
            ("privacidad", "Qué guardo de ti"))
FAILURE = Answer(QueryIntent.STRUCTURED, "failed", "abstained", [Section(None, [
    "No pude procesar tu consulta por un error interno. Inténtalo de nuevo en unos minutos."])])


class BotService:
    def __init__(self, api: BotApi, intake: TelegramWebhook, rpc: RpcClient, engine: AnswerEngine, *,
                 worker_id: str, message_limit: int = 4096) -> None:
        self.api = api
        self.intake = intake
        self.rpc = rpc
        self.queue = JobQueue(rpc)
        self.engine = engine
        self.worker_id = worker_id
        self.message_limit = message_limit

    # -- recepción -----------------------------------------------------------------------------

    def poll_once(self, offset: int | None, *, timeout: int = 25) -> int | None:
        for raw in self.api.get_updates(offset, timeout=timeout):
            offset = int(raw["update_id"]) + 1
            try:
                update = TgUpdate.model_validate(raw)
            except ValidationError:
                log.warning("update %s con formato inválido", raw.get("update_id"))
                continue
            result = self.intake.accept(update)
            if result.reply:
                self._reply(result.reply)
            if update.callback_query is not None and result.status == "accepted":
                self.api.answer_callback(update.callback_query.id)  # el MVP no usa botones
        return offset

    def _reply(self, reply: dict[str, Any]) -> None:
        try:
            if reply["method"] == "sendMessage":
                self.api.send_message(reply["chat_id"], reply["text"], html=False)
            elif reply["method"] == "answerCallbackQuery":
                self.api.answer_callback(reply["callback_query_id"], reply.get("text"))
        except TelegramApiError as exc:
            log.warning("no se pudo enviar un rechazo: %s", exc.code)

    # -- procesamiento -------------------------------------------------------------------------

    def drain(self, *, max_jobs: int = 20) -> int:
        done = 0
        while done < max_jobs:
            jobs = self.queue.claim(self.worker_id, KINDS, limit=5, lease_seconds=120)
            if not jobs:
                return done
            for job in jobs:
                self._process(job)
                done += 1
        return done

    def _process(self, job: Job) -> None:
        try:
            if job.kind == "query.telegram_message":
                self._answer(job)
            self.queue.complete(job.id, self.worker_id)
        except Exception as exc:  # noqa: BLE001 — la cola decide el reintento
            log.exception("fallo procesando %s", job.id)
            code = getattr(exc, "code", type(exc).__name__)
            self.queue.fail(job.id, self.worker_id, str(code)[:60], str(exc)[:500])

    def _answer(self, job: Job) -> None:
        query_id = str(job.payload["query_run_id"])
        begun = as_rows(self.rpc.call("query_begin", {"p_query_run_id": query_id}))
        if not begun:
            return
        started = time.monotonic()
        try:
            answer = self.engine.answer(begun[0]["question_text"] or "")
        except Exception:  # noqa: BLE001 — se responde al usuario y se registra el fallo
            log.exception("el motor falló en la consulta %s", query_id)
            answer = FAILURE
        segments = split_message(render_sections(answer.sections), self.message_limit) or ["Sin respuesta."]
        deliveries = as_rows(self.rpc.call("query_finish", {
            "p_query_run_id": query_id, "p_intent": answer.intent.value, "p_status": answer.status,
            "p_answer_text": plain_text(answer)[:20000], "p_support_status": answer.support,
            "p_warnings": answer.warnings, "p_segments": segments[:20],
            "p_latency_ms": int((time.monotonic() - started) * 1000),
        }))
        for delivery in deliveries:
            if delivery["state"] == "sent":
                continue
            self._deliver(delivery)

    def _deliver(self, delivery: dict[str, Any]) -> None:
        body = delivery["body"]
        try:
            try:
                sent = self.api.send_message(int(delivery["chat_id"]), body)
            except TelegramApiError as exc:
                if exc.status != 400:
                    raise
                # HTML rechazado por Telegram: se entrega el mismo contenido como texto plano.
                sent = self.api.send_message(int(delivery["chat_id"]), html.unescape(re.sub(r"<[^>]+>", "", body)),
                                             html=False)
            self.rpc.call("delivery_mark", {"p_delivery_id": delivery["delivery_id"], "p_state": "sent",
                                            "p_provider_message_id": sent.message_id, "p_error_code": None})
        except TelegramApiError as exc:
            state = "uncertain" if exc.code == "TIMEOUT" else "failed"
            self.rpc.call("delivery_mark", {"p_delivery_id": delivery["delivery_id"], "p_state": state,
                                            "p_provider_message_id": None, "p_error_code": exc.code})
            if exc.kind in (FailureKind.TRANSIENT, FailureKind.RATE_LIMITED):
                raise

    # -- bucle ---------------------------------------------------------------------------------

    def run_forever(self) -> None:
        self.api.delete_webhook()
        self.api.set_commands(COMMANDS)
        offset: int | None = None
        backoff = 1.0
        while True:
            try:
                offset = self.poll_once(offset)
                self.drain()
                backoff = 1.0
            except TelegramApiError as exc:
                log.warning("Telegram: %s; reintento en %.0f s", exc.code, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception:  # noqa: BLE001 — el servicio no se detiene por un fallo puntual
                log.exception("error en el ciclo del bot; reintento en %.0f s", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

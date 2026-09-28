"""Ejecución de la interfaz: texto y clics → vistas entregadas (centrorequirement.md §19, §22; DEC-18).

* Texto: `telegram_accept_update` (deduplica, autoriza, limita y crea consulta + trabajo) → el
  trabajador toma el trabajo → vista → `query_finish` → envío → `delivery_mark`. Así cada pregunta
  queda trazada como antes y un fallo de envío no repite el cómputo.
* Clic: acuse inmediato (< 1 s) → deduplicación → token → dueño → autorización en caché → vista →
  edición del mensaje. Los clics no crean consultas en InsForge (DEC-18: eventos a logs locales).
* El contexto se guarda con compare-and-swap; si otra interacción cambió la sesión, la vista
  vieja no pisa a la nueva (UI-F28).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from botcentro.insforge.client import RpcClient, as_rows
from botcentro.jobs.queue import JobQueue
from botcentro.telegram.render import split_message
from botcentro.telegram.security import pseudonymize_user
from botcentro.telegram.webhook import TelegramWebhook, TgUpdate
from botcentro.telegram_ui.app import HOME_BUTTON, UiApplication
from botcentro.telegram_ui.contracts import Button, CallbackRecord, SessionContext, UiAction, ViewModel
from botcentro.telegram_ui.intents import parse_text
from botcentro.telegram_ui.state import UiState

log = logging.getLogger("botcentro.ui")
KINDS = ("query.telegram_message", "query.telegram_callback")
Keyboard = list[list[dict[str, str]]]  # [[{"text": ..., "callback_data": ...} | {"text": ..., "url": ...}]]


class Transport(Protocol):
    async def send(self, chat_id: int, html: str, keyboard: Keyboard | None) -> int: ...
    async def edit(self, chat_id: int, message_id: int, html: str, keyboard: Keyboard | None) -> bool: ...
    async def answer_callback(self, callback_id: str, text: str | None = None, alert: bool = False) -> None: ...


@dataclass
class Principal:
    chat_id: int
    user_id: int
    user_hash: str


class UiRuntime:
    def __init__(self, *, transport: Transport, state: UiState, app: UiApplication, intake: TelegramWebhook,
                 rpc: RpcClient, bot_id: int, pseudonym_key: bytes, worker_id: str,
                 message_limit: int = 4096, clock: Callable[[], float] = time.monotonic) -> None:
        self.transport = transport
        self.state = state
        self.app = app
        self.intake = intake
        self.rpc = rpc
        self.queue = JobQueue(rpc)
        self.bot_id = bot_id
        self.key = pseudonym_key
        self.worker_id = worker_id
        self.message_limit = message_limit
        self.clock = clock
        self._drain_lock = asyncio.Lock()

    def principal(self, chat_id: int, user_id: int) -> Principal:
        return Principal(chat_id, user_id, pseudonymize_user(self.key, self.bot_id, user_id))

    def _event(self, name: str, who: Principal, **fields: Any) -> None:
        log.info(json.dumps({"event": name, "user": who.user_hash[:12], **fields}, ensure_ascii=False, default=str))

    # -- texto ---------------------------------------------------------------------------------

    async def on_update(self, raw: dict[str, Any]) -> None:
        """Punto de entrada común: un update de Telegram tal como llega de getUpdates."""
        update = TgUpdate.model_validate(raw)
        if update.callback_query is not None:
            cq = update.callback_query
            chat = cq.message.chat.id if cq.message else cq.from_.id
            await self.on_callback(cq.id, chat, cq.from_.id, cq.message.message_id if cq.message else None, cq.data)
            return
        result = await asyncio.to_thread(self.intake.accept, update)
        if result.reply and result.reply.get("method") == "sendMessage":
            await self.transport.send(result.reply["chat_id"], result.reply["text"], None)
        if result.status == "accepted" and not result.duplicate:
            await self.drain()

    async def drain(self, *, max_jobs: int = 20) -> int:
        async with self._drain_lock:  # un solo trabajador de consultas por proceso
            done = 0
            while done < max_jobs:
                jobs = await asyncio.to_thread(self.queue.claim, self.worker_id, KINDS, limit=5, lease_seconds=120)
                if not jobs:
                    return done
                for job in jobs:
                    try:
                        if job.kind == "query.telegram_message":
                            await self._answer(str(job.payload["query_run_id"]))
                        await asyncio.to_thread(self.queue.complete, job.id, self.worker_id)
                    except Exception as exc:  # noqa: BLE001 — la cola decide el reintento
                        log.exception("fallo procesando %s", job.id)
                        await asyncio.to_thread(self.queue.fail, job.id, self.worker_id,
                                                str(getattr(exc, "code", type(exc).__name__))[:60], str(exc)[:500])
                    done += 1
            return done

    async def _answer(self, query_id: str) -> None:
        begun = as_rows(await asyncio.to_thread(self.rpc.call, "query_begin", {"p_query_run_id": query_id}))
        if not begun:
            return
        chat_id = int(begun[0]["chat_id"])
        who = self.principal(chat_id, chat_id)  # chat privado: chat = usuario
        started = self.clock()
        action = parse_text(begun[0]["question_text"] or "")
        ctx = await self.state.load(who.chat_id, who.user_id)
        result_set = await self._result_set(who, action, ctx)
        try:
            view = await asyncio.to_thread(self.app.handle, action, ctx, result_set)
            await self._store_result_set(who, view)
            status, support = _status(view)
        except Exception:  # noqa: BLE001 — se responde al usuario y se registra
            log.exception("fallo de la vista en la consulta %s", query_id)
            view = ViewModel(view_type="error", title="Error", status="error", rows=[[HOME_BUTTON]],
                             blocks=["No pude procesar tu consulta por un error interno. Inténtalo de nuevo."])
            status, support = "failed", "abstained"
        segments = split_message(view.blocks or [view.title], self.message_limit)[:20]
        deliveries = as_rows(await asyncio.to_thread(self.rpc.call, "query_finish", {
            "p_query_run_id": query_id, "p_intent": _query_intent(action), "p_status": status,
            "p_answer_text": _plain("\n\n".join(segments))[:20000], "p_support_status": support,
            "p_warnings": [], "p_segments": segments, "p_latency_ms": int((self.clock() - started) * 1000)}))
        keyboard = await self._keyboard(view, who, ctx)
        last_id = None
        for i, delivery in enumerate(deliveries):
            if delivery["state"] == "sent":
                continue
            markup = keyboard if i == len(deliveries) - 1 else None
            try:
                last_id = await self.transport.send(chat_id, delivery["body"], markup)
                await asyncio.to_thread(self.rpc.call, "delivery_mark", {
                    "p_delivery_id": delivery["delivery_id"], "p_state": "sent", "p_provider_message_id": last_id,
                    "p_error_code": None})
            except Exception as exc:  # noqa: BLE001 — se registra el estado; la consulta no se repite
                await asyncio.to_thread(self.rpc.call, "delivery_mark", {
                    "p_delivery_id": delivery["delivery_id"], "p_state": "failed", "p_provider_message_id": None,
                    "p_error_code": str(getattr(exc, "code", type(exc).__name__))[:60]})
                raise
        if last_id is not None:
            ctx.anchor_message_id = last_id
        await self._save(who, ctx)
        self._event("ui.view_rendered", who, entry=action.entry_point, intent=action.intent.value,
                    view=view.view_type, status=view.status, ms=int((self.clock() - started) * 1000))

    # -- clics ---------------------------------------------------------------------------------

    async def on_callback(self, callback_id: str, chat_id: int, user_id: int, message_id: int | None,
                          data: str | None) -> None:
        started = self.clock()
        who = self.principal(chat_id, user_id)
        if not await self.state.first_time(f"cb:{callback_id}"):
            await self.transport.answer_callback(callback_id)
            return
        record = await self.state.resolve_callback(data)
        if record is None:
            await self.transport.answer_callback(callback_id, "Este menú venció. Te abro el inicio.")
            self._event("ui.callback_rejected", who, reason="expired")
            await self._show(who, UiAction(intent=HOME_BUTTON.intent, entry_point="button"), None, started)
            return
        if record.owner_user_id != user_id or record.chat_id != chat_id:
            await self.transport.answer_callback(callback_id, "Este menú es de otra persona. Escribe /start para "
                                                              "abrir el tuyo.", alert=True)
            self._event("ui.callback_rejected", who, reason="owner")
            return
        if not await self._authorized(who):
            await self.transport.answer_callback(callback_id, "Tu acceso al piloto no está vigente.", alert=True)
            self._event("ui.callback_rejected", who, reason="unauthorized")
            return
        await self.transport.answer_callback(callback_id)  # acuse antes del trabajo lento (UI-O04)
        action = UiAction(intent=record.intent, entry_point="button", parameters=dict(record.params))
        await self._show(who, action, message_id, started)

    async def _show(self, who: Principal, action: UiAction, message_id: int | None, started: float) -> None:
        ctx = await self.state.load(who.chat_id, who.user_id)
        revision = ctx.revision
        result_set = await self._result_set(who, action, ctx)
        view = await asyncio.to_thread(self.app.handle, action, ctx, result_set)
        latest = await self.state.load(who.chat_id, who.user_id)
        if latest.revision != revision:
            # Otra interacción cambió la sesión mientras se armaba esta vista: no se pisa (UI-F28).
            self._event("ui.render_superseded", who, intent=action.intent.value)
            return
        await self._store_result_set(who, view)
        keyboard = await self._keyboard(view, who, ctx)
        html = split_message(view.blocks or [view.title], self.message_limit)[0]
        edited = message_id is not None and await self.transport.edit(who.chat_id, message_id, html, keyboard)
        ctx.anchor_message_id = message_id if edited else await self.transport.send(who.chat_id, html, keyboard)
        await self._save(who, ctx)
        self._event("ui.view_rendered", who, entry="button", intent=action.intent.value, view=view.view_type,
                    status=view.status, edited=bool(edited), ms=int((self.clock() - started) * 1000))

    # -- apoyo ---------------------------------------------------------------------------------

    async def _result_set(self, who: Principal, action: UiAction, ctx: SessionContext):  # noqa: ANN202
        rs_id = action.parameters.get("result_set_id") or ctx.result_set_id
        return await self.state.get_result_set(who.chat_id, who.user_id, rs_id) if rs_id else None

    async def _store_result_set(self, who: Principal, view: ViewModel) -> None:
        if view.new_result_set is not None:
            await self.state.save_result_set(who.chat_id, who.user_id, view.new_result_set)

    async def _authorized(self, who: Principal) -> bool:
        cached = await self.state.cached_authorization(who.user_hash)
        if cached is not None:
            return cached
        ok = bool(await asyncio.to_thread(self.rpc.call, "bot_identity_authorized",
                                          {"p_bot_id": self.bot_id, "p_user_hash": who.user_hash}))
        await self.state.cache_authorization(who.user_hash, ok)
        return ok

    async def _keyboard(self, view: ViewModel, who: Principal, ctx: SessionContext) -> Keyboard | None:
        rows: Keyboard = []
        for row in view.rows:
            buttons = []
            for button in row:
                buttons.append(await self._button(button, who, ctx))
            if buttons:
                rows.append(buttons)
        return rows or None

    async def _button(self, button: Button, who: Principal, ctx: SessionContext) -> dict[str, str]:
        if button.url:
            return {"text": button.label, "url": button.url}
        data = await self.state.issue_callback(CallbackRecord(
            owner_user_id=who.user_id, chat_id=who.chat_id, intent=button.intent, params=button.params,
            session_id=ctx.session_id, view_revision=ctx.revision))
        return {"text": button.label, "callback_data": data}

    async def _save(self, who: Principal, ctx: SessionContext) -> None:
        if not await self.state.save(who.chat_id, who.user_id, ctx):
            self._event("ui.context_conflict", who)


def _status(view: ViewModel) -> tuple[str, str]:
    return {"ready": ("answered", "supported"), "partial": ("needs_clarification", "abstained"),
            "empty": ("insufficient_evidence", "abstained"), "error": ("failed", "abstained"),
            "unavailable": ("answered", "abstained")}.get(view.status, ("answered", "partially_supported"))


def _query_intent(action: UiAction) -> str:
    """query_runs.intent admite el vocabulario del SRS; la navegación cuenta como consulta estructurada."""
    return {"agenda.list": "agenda", "question.free": "structured"}.get(action.intent.value, "structured")


def _plain(html_text: str) -> str:
    import html
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", html_text))


def keyboard_texts(keyboard: Sequence[Sequence[dict[str, str]]] | None) -> list[str]:
    return [b["text"] for row in keyboard or [] for b in row]

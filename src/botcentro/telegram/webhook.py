"""Recepción de actualizaciones de Telegram (SRS-F22, §11; T-21).

Secuencia: autenticar la entrega → interpretar → seudonimizar → persistir actualización,
consulta y trabajo en una sola RPC (telegram_accept_update) → confirmar a Telegram.
El procesamiento ocurre fuera del webhook. Los rechazos se responden en la misma respuesta
HTTP del webhook (método sendMessage/answerCallbackQuery), sin almacenar datos del usuario.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from botcentro.insforge.client import RpcClient, as_rows
from botcentro.telegram.security import pseudonymize_user, verify_webhook_secret

log = logging.getLogger(__name__)


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TgUser(_Model):
    id: int
    is_bot: bool = False


class TgChat(_Model):
    id: int
    type: str


class TgMessage(_Model):
    message_id: int
    date: int
    chat: TgChat
    from_: TgUser | None = Field(default=None, alias="from")
    text: str | None = None


class TgCallbackQuery(_Model):
    id: str
    from_: TgUser = Field(alias="from")
    message: TgMessage | None = None
    data: str | None = None


class TgUpdate(_Model):
    update_id: int
    message: TgMessage | None = None
    callback_query: TgCallbackQuery | None = None


class WebhookUnauthorized(Exception):
    """La entrega no trae el secreto correcto: no proviene de Telegram."""


@dataclass(frozen=True)
class WebhookSettings:
    bot_id: int
    secret: str
    pseudonym_key: bytes
    rate_limit_per_minute: int = 10


@dataclass(frozen=True)
class WebhookResult:
    status: str
    duplicate: bool = False
    reply: dict[str, Any] | None = None


REJECTION_TEXT = {
    "rejected_unauthorized": (
        "Este bot funciona en un piloto privado y tu cuenta aún no está autorizada. "
        "Si participas en el piloto, comparte con el equipo tu identificador de Telegram: {user_id}."
    ),
    "rejected_unsupported": "Por ahora solo respondo mensajes de texto en chats privados.",
    "rejected_rate_limited": "Recibí muchas consultas seguidas. Espera un minuto e inténtalo de nuevo.",
}


def _classify(update: TgUpdate) -> tuple[str, TgUser | None, TgChat | None, str | None, str | None]:
    """(tipo, usuario, chat, texto o datos, id de callback)."""
    if update.message is not None:
        msg = update.message
        kind = "message" if msg.text else "other"  # adjuntos y voz: no soportados en el MVP
        return kind, msg.from_, msg.chat, msg.text, None
    if update.callback_query is not None:
        cq = update.callback_query
        chat = cq.message.chat if cq.message else None
        return "callback_query", cq.from_, chat, cq.data, cq.id
    return "other", None, None, None, None


class TelegramWebhook:
    def __init__(self, settings: WebhookSettings, rpc: RpcClient) -> None:
        self.settings = settings
        self._rpc = rpc

    def handle(self, secret_header: str | None, body: bytes) -> WebhookResult:
        if not verify_webhook_secret(secret_header, self.settings.secret):
            raise WebhookUnauthorized("secreto de webhook inválido")
        try:
            update = TgUpdate.model_validate_json(body)
        except ValidationError:
            # Autenticada pero ilegible: se registra y se confirma para no bloquear la cola de
            # entregas de Telegram con un update que nunca será válido.
            log.error("update de Telegram con formato inválido")
            return WebhookResult("ignored")

        kind, user, chat, text, callback_id = _classify(update)
        if user is None or user.is_bot or chat is None:
            return WebhookResult("ignored")

        row = as_rows(self._rpc.call("telegram_accept_update", {
            "p_bot_id": self.settings.bot_id,
            "p_update_id": update.update_id,
            "p_update_kind": kind,
            "p_user_hash": pseudonymize_user(self.settings.pseudonym_key, self.settings.bot_id, user.id),
            "p_chat_type": chat.type,
            "p_text": text,
            "p_rate_limit_per_minute": self.settings.rate_limit_per_minute,
        }))[0]
        status, duplicate = row["status"], bool(row["duplicate"])
        reply = None
        if not duplicate and status in REJECTION_TEXT:
            message = REJECTION_TEXT[status].format(user_id=user.id)
            if callback_id is not None:
                reply = {"method": "answerCallbackQuery", "callback_query_id": callback_id,
                         "text": message[:200], "show_alert": True}
            elif chat.type == "private":
                reply = {"method": "sendMessage", "chat_id": chat.id, "text": message}
        return WebhookResult(status, duplicate, reply)

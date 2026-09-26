"""Seguridad del canal Telegram (SRS-F22, F23, §11; T-21, T-22).

* Autenticación del webhook con el secreto que Telegram envía en la cabecera
  X-Telegram-Bot-Api-Secret-Token (configurado con setWebhook secret_token).
* Seudonimización: el ID de usuario de Telegram no se guarda; se guarda HMAC(clave, bot:usuario).
* Callbacks: tokens compactos firmados, ligados a usuario y chat, con expiración (≤ 64 bytes).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
CALLBACK_DATA_LIMIT = 64
_TOKEN_VERSION = 1
_MAC_BYTES = 12
_PAYLOAD = struct.Struct(">BB16sI")  # versión, acción, referencia UUID, expiración (epoch s)


def verify_webhook_secret(received: str | None, expected: str) -> bool:
    """Comparación en tiempo constante. Un secreto no configurado nunca autentica."""
    if not expected or len(expected) < 16:
        raise ValueError("el secreto del webhook debe configurarse (≥ 16 caracteres)")
    if received is None:
        return False
    return hmac.compare_digest(received.encode("utf-8"), expected.encode("utf-8"))


def pseudonymize_user(key: bytes, bot_id: int, user_id: int) -> str:
    """Identificador estable por bot, no reversible sin la clave (SRS §11, N05)."""
    if len(key) < 32:
        raise ValueError("la clave de seudonimización debe tener al menos 32 bytes")
    return hmac.new(key, f"tg:{bot_id}:{user_id}".encode(), hashlib.sha256).hexdigest()


class CallbackAction(IntEnum):
    CHOOSE_OPTION = 1  # respuesta a una aclaración: ref = contexto de chat
    MORE_DETAIL = 2  # ref = respuesta
    SHOW_SOURCES = 3  # ref = respuesta


@dataclass(frozen=True)
class CallbackPayload:
    action: CallbackAction
    ref: UUID
    expires_at: int


class CallbackRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _mac(key: bytes, payload: bytes, user_id: int, chat_id: int) -> bytes:
    bound = payload + struct.pack(">qq", user_id, chat_id)
    return hmac.new(key, bound, hashlib.sha256).digest()[:_MAC_BYTES]


def sign_callback(key: bytes, action: CallbackAction, ref: UUID, *, user_id: int, chat_id: int,
                  ttl_seconds: int = 24 * 3600, now: float | None = None) -> str:
    expires = int((now if now is not None else time.time()) + ttl_seconds)
    payload = _PAYLOAD.pack(_TOKEN_VERSION, int(action), ref.bytes, expires)
    token = base64.urlsafe_b64encode(payload + _mac(key, payload, user_id, chat_id)).rstrip(b"=").decode("ascii")
    assert len(token) <= CALLBACK_DATA_LIMIT
    return token


def verify_callback(key: bytes, token: str, *, user_id: int, chat_id: int, now: float | None = None) -> CallbackPayload:
    """Rechaza tokens manipulados, de otro usuario/chat o vencidos."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError) as exc:
        raise CallbackRejected("MALFORMED", "callback ilegible") from exc
    if len(raw) != _PAYLOAD.size + _MAC_BYTES:
        raise CallbackRejected("MALFORMED", "callback de longitud inválida")
    payload, mac = raw[: _PAYLOAD.size], raw[_PAYLOAD.size:]
    if not hmac.compare_digest(mac, _mac(key, payload, user_id, chat_id)):
        raise CallbackRejected("FORBIDDEN", "callback no pertenece a este usuario o fue alterado")
    version, action, ref, expires = _PAYLOAD.unpack(payload)
    if version != _TOKEN_VERSION:
        raise CallbackRejected("MALFORMED", "versión de callback desconocida")
    if (now if now is not None else time.time()) > expires:
        raise CallbackRejected("EXPIRED", "la opción expiró; repite la consulta")
    try:
        return CallbackPayload(CallbackAction(action), UUID(bytes=ref), expires)
    except ValueError as exc:
        raise CallbackRejected("MALFORMED", "acción de callback desconocida") from exc

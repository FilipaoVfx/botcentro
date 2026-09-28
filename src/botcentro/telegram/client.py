"""Cliente mínimo de la Bot API de Telegram (SRS §11).

Se usa sondeo largo (getUpdates): el servidor del bot no necesita URL pública (DEC-16). El
token solo vive en `.env` y nunca se registra: las URL de la API lo contienen, así que los
errores se reportan sin la URL.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from botcentro.errors import BotcentroError, FailureKind

API = "https://api.telegram.org"


class TelegramApiError(BotcentroError):
    """Fallo de la Bot API; `retry_after` viene de la respuesta 429 de Telegram."""


@dataclass(frozen=True)
class SentMessage:
    message_id: int


class BotApi:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 30.0) -> None:
        if ":" not in token:
            raise ValueError("token de bot inválido")
        self.bot_id = int(token.split(":", 1)[0])
        self._http = httpx.Client(base_url=f"{API}/bot{token}", timeout=timeout, transport=transport,
                                  trust_env=False)

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, payload: Mapping[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        try:
            response = self._http.post(f"/{method}", json=dict(payload or {}),
                                       timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.TimeoutException as exc:
            raise TelegramApiError("TIMEOUT", f"{method}: tiempo de espera agotado",
                                   kind=FailureKind.TRANSIENT) from exc
        except httpx.TransportError as exc:
            raise TelegramApiError("NETWORK_ERROR", f"{method}: error de red ({type(exc).__name__})",
                                   kind=FailureKind.TRANSIENT) from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and body.get("ok"):
            return body.get("result")
        description = str(body.get("description") or response.text[:200])
        if response.status_code == 429:
            retry = float((body.get("parameters") or {}).get("retry_after", 5))
            raise TelegramApiError("RATE_LIMITED", f"{method}: {description}", kind=FailureKind.RATE_LIMITED,
                                   status=429, retry_after=retry)
        kind = FailureKind.TRANSIENT if response.status_code >= 500 else FailureKind.PERMISSION \
            if response.status_code in (401, 403) else FailureKind.INVALID_CONTENT
        raise TelegramApiError(f"HTTP_{response.status_code}", f"{method}: {description}", kind=kind,
                               status=response.status_code)

    def get_me(self) -> dict[str, Any]:
        return dict(self._call("getMe"))

    def delete_webhook(self) -> None:
        """El sondeo largo y el webhook son excluyentes en Telegram."""
        self._call("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(self, offset: int | None, *, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return list(self._call("getUpdates", payload, timeout=timeout + 10))

    def send_message(self, chat_id: int, text: str, *, html: bool = True) -> SentMessage:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text,
                                   "link_preview_options": {"is_disabled": True}}
        if html:
            payload["parse_mode"] = "HTML"
        for attempt in range(3):
            try:
                result = self._call("sendMessage", payload)
                return SentMessage(int(result["message_id"]))
            except TelegramApiError as exc:
                if exc.code != "RATE_LIMITED" or attempt == 2:
                    raise
                time.sleep(min(exc.retry_after or 5, 30))
        raise AssertionError("inalcanzable")

    def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        self._call("answerCallbackQuery", payload)

    def set_commands(self, commands: Sequence[tuple[str, str]]) -> None:
        self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})

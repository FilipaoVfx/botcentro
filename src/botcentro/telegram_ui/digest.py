"""Resumen diario de seguimientos y correcciones (investigaciones §14, SUB-01..06).

* Resumen: una vez al día desde las 18:00 de Bogotá; si el proceso estuvo caído hasta las 21:00, el
  resumen espera al día siguiente (no se envía en la ventana silenciosa).
* Ventana silenciosa 21:00–08:00: no se envía nada. Las correcciones salen en la siguiente ventana
  permitida, sin esperar las 18:00.
* `digest_batch` vuelve a comprobar consentimiento y visibilidad antes de entregar (SUB-04).
* Estados de entrega: `sending` antes de llamar a Telegram; `sent`, `failed` o `unknown_delivery`
  después. Una entrega ambigua (tiempo agotado) no se reintenta a ciegas (SUB-05).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from botcentro.domain.dates import BOGOTA
from botcentro.insforge.client import RpcClient, as_rows
from botcentro.telegram.render import bold, escape

log = logging.getLogger("botcentro.digest")


class _Sender(Protocol):
    async def send(self, chat_id: int, html: str, keyboard: Any) -> int: ...


class _Store(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any) -> Any: ...


@dataclass(frozen=True)
class DigestPolicy:
    hour: int = 18
    quiet_start: int = 21
    quiet_end: int = 8

    def quiet(self, now: datetime) -> bool:
        return now.hour >= self.quiet_start or now.hour < self.quiet_end

    def digest_due(self, now: datetime, last_digest: date | None) -> bool:
        return not self.quiet(now) and now.hour >= self.hour and last_digest != now.date()


def delivery_error(exc: BaseException) -> tuple[str, str]:
    """(estado, código) para una excepción del envío."""
    name = type(exc).__name__
    text = str(exc).lower()
    if "Forbidden" in name or "bot was blocked" in text or "user is deactivated" in text:
        return "failed", "BOT_BLOCKED"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in name or "NetworkError" in name:
        return "unknown_delivery", "TIMEOUT"
    return "failed", name[:60]


def render_digest(items: list[dict[str, Any]]) -> str:
    corrections = [i for i in items if i.get("event_type") == "correction"]
    news = [i for i in items if i.get("event_type") != "correction"]
    lines: list[str] = []
    if corrections:
        lines += [bold("⚠️ Corrección"), "Un contenido que te enviamos fue corregido o retirado tras revisión:"]
        lines += [f"• {escape(i.get('summary') or '')}" for i in corrections[:10]]
        lines.append("")
    if news:
        lines += [bold("🔔 Novedades de tus seguimientos"), "<i>Publicado tras revisión editorial.</i>"]
        lines += [f"• {escape(i.get('summary') or '')}" for i in news[:15]]
        if len(news) > 15:
            lines.append(f"… y {len(news) - 15} novedades más.")
        lines.append("")
    lines.append("<i>Gestiona o cancela en /seguimientos.</i>")
    return "\n".join(lines)


class DigestWorker:
    LAST_KEY = "digest:last_day"

    def __init__(self, rpc: RpcClient, sender: _Sender, store: _Store, *, prefix: str,
                 policy: DigestPolicy = DigestPolicy(), now: Callable[[], datetime] | None = None) -> None:
        self.rpc = rpc
        self.sender = sender
        self.store = store
        self.key = f"{prefix}:{self.LAST_KEY}"
        self.policy = policy
        self.now = now or (lambda: datetime.now(BOGOTA))

    async def tick(self) -> dict[str, int]:
        now = self.now()
        if self.policy.quiet(now):
            return {"sent": 0, "failed": 0, "unknown": 0}
        raw = await self.store.get(self.key)
        last = date.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw) if raw else None
        include = self.policy.digest_due(now, last)
        if include:
            await asyncio.to_thread(self.rpc.call, "digest_prepare", {})
        stats = {"sent": 0, "failed": 0, "unknown": 0}
        batch = as_rows(await asyncio.to_thread(self.rpc.call, "digest_batch",
                                                {"p_include_digest": include, "p_limit": 50}))
        for group in batch:
            ids = [i["delivery_id"] for i in group["items"]]
            await asyncio.to_thread(self.rpc.call, "digest_mark", {"p_delivery_ids": ids, "p_state": "sending",
                                                                   "p_message_id": None, "p_error": None})
            try:
                message_id = await self.sender.send(int(group["chat_id"]), render_digest(group["items"]), None)
            except Exception as exc:  # noqa: BLE001 — el estado queda registrado
                state, code = delivery_error(exc)
                log.warning("entrega de resumen %s: %s", state, code)
                await asyncio.to_thread(self.rpc.call, "digest_mark", {"p_delivery_ids": ids, "p_state": state,
                                                                       "p_message_id": None, "p_error": code})
                stats["unknown" if state == "unknown_delivery" else "failed"] += 1
                continue
            await asyncio.to_thread(self.rpc.call, "digest_mark", {"p_delivery_ids": ids, "p_state": "sent",
                                                                   "p_message_id": message_id, "p_error": None})
            stats["sent"] += 1
        if include:
            await self.store.set(self.key, now.date().isoformat())
        return stats

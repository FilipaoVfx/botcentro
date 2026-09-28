"""Arnés compartido para probar el bot contra SQL y Redis reales (transporte e índice en memoria)."""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from itertools import count

from botcentro.domain.dates import BOGOTA
from botcentro.query.engine import AnswerEngine
from botcentro.telegram.security import pseudonymize_user
from botcentro.telegram.webhook import TelegramWebhook, WebhookSettings
from botcentro.telegram_ui.app import UiApplication
from botcentro.telegram_ui.runtime import UiRuntime
from botcentro.telegram_ui.state import UiState

BOT_ID = 777
KEY = b"k" * 32
ALLOWED, STRANGER, OTHER_ALLOWED = 1001, 2002, 3003
REDIS_URL = os.environ.get("BOTCENTRO_TEST_REDIS_URL")


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.answers: list[dict] = []
        self._ids = count(1)

    async def send(self, chat_id, html, keyboard):
        message_id = next(self._ids)
        self.sent.append({"chat": chat_id, "html": html, "keyboard": keyboard, "id": message_id})
        return message_id

    async def edit(self, chat_id, message_id, html, keyboard):
        self.edits.append({"chat": chat_id, "id": message_id, "html": html, "keyboard": keyboard})
        return True

    async def answer_callback(self, callback_id, text=None, alert=False):
        self.answers.append({"id": callback_id, "text": text, "alert": alert})


class FakeVectors:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, vector, *, limit=8, flt=None, with_payload=True):
        self.calls.append(flt)
        return self.hits[:limit]

    def scroll(self, *, offset=None, limit=256, flt=None, with_payload=True, with_vector=True):
        return [h for h in self.hits if h["payload"]["doc_kind"] == "gaceta"], None


class Harness:
    def __init__(self, runtime: UiRuntime, transport: FakeTransport, vectors: FakeVectors) -> None:
        self.runtime, self.transport, self.vectors = runtime, transport, vectors
        self._updates = count(10_000)
        self._callbacks = count(1)
        self.loop = asyncio.new_event_loop()

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def say(self, text: str, user: int = ALLOWED, update_id: int | None = None) -> list[dict]:
        before = len(self.transport.sent)
        uid = update_id or next(self._updates)
        self.run(self.runtime.on_update({"update_id": uid, "message": {
            "message_id": uid, "date": 0, "text": text, "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False}}}))
        return self.transport.sent[before:]

    def click(self, message: dict, label: str, user: int = ALLOWED, callback_id: str | None = None) -> str:
        data = next(b["callback_data"] for row in message["keyboard"] for b in row if b["text"] == label)
        cid = callback_id or f"cb{next(self._callbacks)}"
        self.run(self.runtime.on_update({"update_id": next(self._updates), "callback_query": {
            "id": cid, "data": data, "from": {"id": user, "is_bot": False},
            "message": {"message_id": message["id"], "date": 0, "chat": {"id": message["chat"], "type": "private"}}}}))
        return cid



def build_harness(db, vectors: FakeVectors, *, namespace: str) -> tuple[Harness, object]:
    """Autoriza a los usuarios de prueba y arma el runtime completo. Devuelve (arnés, cliente Redis)."""
    from redis.asyncio import Redis

    admin, query = db.create_user("admin"), db.create_user("query_service")
    for user in (ALLOWED, OTHER_ALLOWED):
        db.rpc(admin).call("admin_authorize_telegram", {
            "p_bot_id": BOT_ID, "p_user_hash": pseudonymize_user(KEY, BOT_ID, user), "p_chat_id": user,
            "p_label": "piloto", "p_reason": "prueba de integración"})
    rpc = db.rpc(query)
    engine = AnswerEngine(rpc, vectors, lambda text: [0.0] * 384,
                          clock=lambda: datetime(2026, 9, 28, 10, tzinfo=BOGOTA))
    redis = Redis.from_url(REDIS_URL)
    runtime = UiRuntime(transport=FakeTransport(), state=UiState(redis, bot_id=BOT_ID, namespace=namespace),
                        app=UiApplication(engine, today=lambda: date(2026, 9, 28)),
                        intake=TelegramWebhook(WebhookSettings(BOT_ID, "no-se-usa", KEY, rate_limit_per_minute=500), rpc),
                        rpc=rpc, bot_id=BOT_ID, pseudonym_key=KEY, worker_id="test-bot")
    harness = Harness(runtime, runtime.transport, vectors)  # type: ignore[arg-type]
    return harness, redis

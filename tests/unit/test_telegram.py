"""Canal Telegram: autenticación, seudónimos, callbacks (T-22), render y webhook HTTP (T-21)."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from botcentro.api.app import create_app
from botcentro.telegram.render import Section, escape, link, render_sections, split_message, utf16_len
from botcentro.telegram.security import (
    CALLBACK_DATA_LIMIT,
    CallbackAction,
    CallbackRejected,
    pseudonymize_user,
    sign_callback,
    verify_callback,
    verify_webhook_secret,
)
from botcentro.telegram.webhook import TelegramWebhook, WebhookSettings

KEY = b"clave-de-prueba-suficientemente-larga!!"
SECRET = "secreto-del-webhook-123"


def test_webhook_secret() -> None:
    assert verify_webhook_secret(SECRET, SECRET)
    assert not verify_webhook_secret("otro", SECRET)
    assert not verify_webhook_secret(None, SECRET)
    with pytest.raises(ValueError):
        verify_webhook_secret("", "")


def test_pseudonym_is_stable_and_bot_scoped() -> None:
    assert pseudonymize_user(KEY, 1, 42) == pseudonymize_user(KEY, 1, 42)
    assert pseudonymize_user(KEY, 1, 42) != pseudonymize_user(KEY, 2, 42)
    digest = pseudonymize_user(KEY, 1, 42)
    assert len(digest) == 64 and digest != pseudonymize_user(b"otra-clave-de-prueba-tambien-larga!!", 1, 42)


def test_callback_round_trip_and_size() -> None:
    ref = uuid.uuid4()
    token = sign_callback(KEY, CallbackAction.CHOOSE_OPTION, ref, user_id=7, chat_id=7, now=1000)
    assert len(token.encode()) <= CALLBACK_DATA_LIMIT
    payload = verify_callback(KEY, token, user_id=7, chat_id=7, now=1001)
    assert payload.ref == ref and payload.action is CallbackAction.CHOOSE_OPTION


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [({"user_id": 8, "chat_id": 7, "now": 1001}, "FORBIDDEN"),
     ({"user_id": 7, "chat_id": 9, "now": 1001}, "FORBIDDEN"),
     ({"user_id": 7, "chat_id": 7, "now": 1000 + 86401}, "EXPIRED")],
)
def test_foreign_or_expired_callbacks_are_rejected(kwargs, code) -> None:
    token = sign_callback(KEY, CallbackAction.MORE_DETAIL, uuid.uuid4(), user_id=7, chat_id=7, now=1000)
    with pytest.raises(CallbackRejected) as info:
        verify_callback(KEY, token, **kwargs)
    assert info.value.code == code


def test_tampered_callback_is_rejected() -> None:
    token = sign_callback(KEY, CallbackAction.MORE_DETAIL, uuid.uuid4(), user_id=7, chat_id=7, now=1000)
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(CallbackRejected):
        verify_callback(KEY, tampered, user_id=7, chat_id=7, now=1001)


def test_render_escapes_and_skips_empty_sections() -> None:
    blocks = render_sections([Section("Estado <legislativo>", [escape("Radicado & publicado")]), Section("Vacía", [])])
    assert blocks == ["<b>Estado &lt;legislativo&gt;</b>\nRadicado &amp; publicado"]


def test_split_never_breaks_a_reference() -> None:
    refs = [f"{i}. " + link(f"Gaceta {i} de 2024, pág. {i}", f"https://leyes.senado.gov.co/g/{i}") for i in range(60)]
    messages = split_message(["Encabezado", "\n".join(refs)], limit=500)
    assert len(messages) > 1
    for message in messages:
        assert utf16_len(message) <= 500
        assert message.count("<a ") == message.count("</a>")
    assert sum(m.count("<a ") for m in messages) == 60


def test_split_counts_utf16_units() -> None:
    emoji_block = "📜" * 300  # 600 unidades UTF-16
    messages = split_message([emoji_block.replace("📜📜", "📜 📜")], limit=256)
    assert all(utf16_len(m) <= 256 for m in messages)


class FakeRpc:
    def __init__(self, status: str = "accepted", duplicate: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status, self.duplicate = status, duplicate

    def call(self, fn, params=None):
        self.calls.append((fn, dict(params or {})))
        if fn == "is_staff_or_service":
            return True
        return [{"status": self.status, "duplicate": self.duplicate, "telegram_identity_id": None,
                 "query_run_id": None, "job_id": None}]


def _client(rpc: FakeRpc) -> TestClient:
    webhook = TelegramWebhook(WebhookSettings(99, SECRET, KEY), rpc)
    return TestClient(create_app(rpc=rpc, telegram=webhook))


def _body(text: str = "hola", chat_type: str = "private") -> str:
    return json.dumps({"update_id": 1, "message": {"message_id": 1, "date": 0, "text": text,
                                                   "chat": {"id": 42, "type": chat_type},
                                                   "from": {"id": 42, "is_bot": False}}})


def test_webhook_rejects_missing_secret_with_uniform_error() -> None:
    response = _client(FakeRpc()).post("/v1/integrations/telegram/webhook", content=_body())
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHENTICATED" and body["request_id"] == response.headers["x-request-id"]


def test_webhook_persists_pseudonymized_update() -> None:
    rpc = FakeRpc()
    response = _client(rpc).post("/v1/integrations/telegram/webhook", content=_body(),
                                 headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert response.status_code == 200 and response.json() == {"ok": True}
    fn, params = rpc.calls[0]
    assert fn == "telegram_accept_update"
    assert params["p_user_hash"] == pseudonymize_user(KEY, 99, 42)
    assert 42 not in params.values()  # el ID real de Telegram no se envía a la base


def test_webhook_replies_inline_to_unauthorized_users() -> None:
    response = _client(FakeRpc(status="rejected_unauthorized")).post(
        "/v1/integrations/telegram/webhook", content=_body(), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    reply = response.json()
    assert reply["method"] == "sendMessage" and reply["chat_id"] == 42 and "42" in reply["text"]


def test_duplicate_update_gets_no_second_reply() -> None:
    response = _client(FakeRpc(status="rejected_unauthorized", duplicate=True)).post(
        "/v1/integrations/telegram/webhook", content=_body(), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert response.json() == {"ok": True}


def test_health_endpoints() -> None:
    client = _client(FakeRpc())
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}

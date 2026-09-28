"""Cliente de la Bot API y entrega de segmentos (DEC-16; SRS-F23)."""

import json

import httpx
import pytest

from botcentro.telegram import client as client_module
from botcentro.telegram.bot import BotService
from botcentro.telegram.client import BotApi, SentMessage, TelegramApiError

TOKEN = "123456:SECRETO-no-debe-aparecer"


def _api(handler) -> BotApi:
    return BotApi(TOKEN, transport=httpx.MockTransport(handler))


def test_bot_id_comes_from_token_and_errors_do_not_leak_it() -> None:
    api = _api(lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    assert api.bot_id == 123456
    with pytest.raises(TelegramApiError) as err:
        api.get_me()
    assert "SECRETO" not in str(err.value) and err.value.status == 401


def test_send_message_waits_on_rate_limit(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(client_module.time, "sleep", lambda s: calls.append(("sleep", s)))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(("post", json.loads(request.content)))
        if len([c for c in calls if c[0] == "post"]) == 1:
            return httpx.Response(429, json={"ok": False, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 3}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    sent = _api(handler).send_message(99, "<b>hola</b>")
    assert sent == SentMessage(42)
    assert ("sleep", 3.0) in calls
    body = calls[0][1]
    assert body["parse_mode"] == "HTML" and body["link_preview_options"] == {"is_disabled": True}


def test_get_updates_uses_long_polling_timeout() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 7}]})

    assert _api(handler).get_updates(5, timeout=25) == [{"update_id": 7}]
    assert seen == {"timeout": 25, "offset": 5, "allowed_updates": ["message", "callback_query"]}


class RejectHtmlApi:
    def __init__(self) -> None:
        self.sent = []

    def send_message(self, chat_id, text, *, html=True):
        if html:
            raise TelegramApiError("HTTP_400", "sendMessage: can't parse entities", status=400)
        self.sent.append(text)
        return SentMessage(1)


class RecordingRpc:
    def __init__(self) -> None:
        self.calls = []

    def call(self, fn, params=None):
        self.calls.append((fn, params))


def test_rejected_html_is_delivered_as_plain_text() -> None:
    api, rpc = RejectHtmlApi(), RecordingRpc()
    service = BotService.__new__(BotService)
    service.api, service.rpc = api, rpc  # type: ignore[assignment]
    service._deliver({"delivery_id": "d1", "chat_id": 5, "body": "<b>Estado</b>: A &amp; B"})
    assert api.sent == ["Estado: A & B"]
    assert rpc.calls == [("delivery_mark", {"p_delivery_id": "d1", "p_state": "sent", "p_provider_message_id": 1,
                                            "p_error_code": None})]

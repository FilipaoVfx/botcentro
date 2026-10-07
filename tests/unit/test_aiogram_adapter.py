"""Adaptador aiogram: detalles de Telegram resueltos sin afectar la lógica (DEC-18; UI-T42, UI-T48)."""

import asyncio

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

from botcentro.telegram_ui.aiogram_adapter import AiogramTransport


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class FakeBot:
    def __init__(self) -> None:
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        if kw.get("parse_mode", "HTML") is not None and "<b" in text:
            raise TelegramBadRequest(method=SendMessage(chat_id=chat_id, text=text),
                                     message="Bad Request: can't parse entities")
        self.sent.append((chat_id, text, reply_markup))
        return FakeMessage(len(self.sent))

    async def edit_message_text(self, **kw):
        raise TelegramBadRequest(method=EditMessageText(text=kw["text"]),
                                 message="Bad Request: message is not modified")

    async def answer_callback_query(self, callback_id, **kw):
        raise TelegramBadRequest(method=AnswerCallbackQuery(callback_query_id=callback_id),
                                 message="Bad Request: query is too old")


def test_rejected_html_is_resent_as_plain_text_with_keyboard() -> None:
    bot = FakeBot()
    transport = AiogramTransport(bot)  # type: ignore[arg-type]
    keyboard = [[{"text": "🏠 Inicio", "callback_data": "v1:abc"}]]
    message_id = asyncio.run(transport.send(5, "<b>Estado</b>: A &amp; B", keyboard))
    assert message_id == 1 and bot.sent[0][1] == "Estado: A & B" and bot.sent[0][2] is not None


def test_not_modified_is_a_successful_edit_and_old_callbacks_do_not_fail() -> None:
    transport = AiogramTransport(FakeBot())  # type: ignore[arg-type]
    assert asyncio.run(transport.edit(5, 1, "igual", None)) is True
    asyncio.run(transport.answer_callback("viejo"))  # no lanza


def test_callback_on_message_with_links_is_forwarded() -> None:
    """Regresión 2026-10-06: un botón en un mensaje con enlaces (actas, videos) fallaba al serializar el update
    porque aiogram completa `link_preview_options` con valores internos no serializables."""
    import asyncio

    from aiogram.types import Update

    from botcentro.telegram_ui.aiogram_adapter import build_dispatcher

    raw = {"update_id": 1, "callback_query": {"id": "1", "from": {"id": 7, "is_bot": False, "first_name": "A"},
           "chat_instance": "x", "data": "v1:abc", "message": {
               "message_id": 5, "date": 1, "chat": {"id": 7, "type": "private"},
               "from": {"id": 9, "is_bot": True, "first_name": "B"}, "text": "Acta 3/2025",
               "entities": [{"type": "text_link", "offset": 0, "length": 5, "url": "https://svrpubindc.imprenta.gov.co/x"}],
               "link_preview_options": {"is_disabled": True}}}}
    received = []

    class Runtime:
        async def on_update(self, update):  # noqa: ANN001, ANN201
            received.append(update)

    from aiogram import Bot

    async def run() -> None:
        bot = Bot("123456:TEST-token-no-network")  # solo se lee su id; el manejador no llama a la API
        try:
            await build_dispatcher(Runtime()).feed_raw_update(bot=bot, update=raw)  # type: ignore[arg-type]
        finally:
            await bot.session.close()

    asyncio.run(run())
    assert received and received[0]["callback_query"]["data"] == "v1:abc"
    assert "Default" not in repr(received[0])

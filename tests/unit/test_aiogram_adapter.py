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

"""Adaptador aiogram 3 (DEC-18): único consumidor de updates del bot, por sondeo largo.

aiogram solo transporta: los updates se entregan crudos a `UiRuntime`, que conserva la
aceptación seudonimizada, la cola y el registro de entregas del backend. Aquí se resuelven los
detalles de Telegram: reintento ante 429, «message is not modified» como no-op, HTML rechazado
reenviado como texto plano y acuse de callbacks vencidos sin error.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

from botcentro.telegram_ui.runtime import Keyboard, UiRuntime

log = logging.getLogger("botcentro.ui.telegram")
COMMANDS = [("start", "Inicio y menú"), ("help", "Qué puedo responder y ejemplos"),
            ("proyecto", "Ficha de un proyecto: /proyecto 178 de 2025 Senado"),
            ("agenda", "Agenda publicada de esta semana"), ("fuentes", "De dónde salen los datos"),
            ("privacidad", "Qué guardo de ti")]


def _markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    if not keyboard:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(**button) for button in row]
                                                 for row in keyboard])


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


class AiogramTransport:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def _retrying(self, call):  # type: ignore[no-untyped-def]
        for attempt in range(3):
            try:
                return await call()
            except TelegramRetryAfter as exc:
                if attempt == 2:
                    raise
                await asyncio.sleep(min(exc.retry_after, 30))

    async def send(self, chat_id: int, text: str, keyboard: Keyboard | None) -> int:
        markup = _markup(keyboard)
        try:
            message = await self._retrying(lambda: self.bot.send_message(chat_id, text, reply_markup=markup))
        except TelegramBadRequest as exc:
            if "parse" not in str(exc).lower() and "entit" not in str(exc).lower():
                raise
            message = await self._retrying(lambda: self.bot.send_message(chat_id, _plain(text), parse_mode=None,
                                                                         reply_markup=markup))
        return int(message.message_id)

    async def edit(self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None) -> bool:
        try:
            await self._retrying(lambda: self.bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id,
                                                                    reply_markup=_markup(keyboard)))
            return True
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                return True  # misma vista: no-op inocuo
            log.info("no se pudo editar el mensaje %s: %s", message_id, exc.message)
            return False  # borrado o demasiado antiguo: el llamador envía uno nuevo

    async def answer_callback(self, callback_id: str, text: str | None = None, alert: bool = False) -> None:
        try:
            await self.bot.answer_callback_query(callback_id, text=text, show_alert=alert)
        except TelegramBadRequest:
            pass  # callback demasiado antiguo: Telegram ya cerró el indicador


def build_dispatcher(runtime: UiRuntime) -> Dispatcher:
    router = Router(name="botcentro")

    async def forward(event_update: Update) -> None:
        raw = event_update.model_dump(mode="json", by_alias=True, exclude_none=True)
        try:
            await runtime.on_update(raw)
        except TelegramForbiddenError:
            log.info("el usuario bloqueó el bot; no se reintenta la entrega")

    @router.message()
    async def on_message(message: Message, event_update: Update) -> None:  # noqa: ARG001
        await forward(event_update)

    @router.callback_query()
    async def on_callback(callback: CallbackQuery, event_update: Update) -> None:  # noqa: ARG001
        await forward(event_update)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


def make_bot(token: str) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))


async def run_polling(bot: Bot, runtime: UiRuntime, *, drain_every: float = 30.0) -> None:
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    dispatcher = build_dispatcher(runtime)

    async def periodic_drain() -> None:  # reintentos de la cola y trabajos que quedaron pendientes
        while True:
            await asyncio.sleep(drain_every)
            try:
                await runtime.drain()
            except Exception:  # noqa: BLE001
                log.exception("fallo drenando la cola")

    task = asyncio.create_task(periodic_drain())
    try:
        await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"], handle_signals=True)
    finally:
        task.cancel()

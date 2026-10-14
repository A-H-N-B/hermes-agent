"""python-telegram-bot glue: a Sender for the dispatcher and the command handlers (C2.2).

Commands are accepted only from TELEGRAM_ADMIN_USER_IDS, in the configured chat or an admin's
private chat with the bot; everything else is ignored and logged. Use a bot token of its own
(sharing one with Hermes Agent makes two long-pollers fight: HTTP 409).
"""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any

import structlog
from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes
from telegram.warnings import PTBDeprecationWarning

from gold_agent.notify.commands import BotCommands
from gold_agent.notify.format import split_message, strip_tags

log = structlog.get_logger(__name__)

MAX_ATTEMPTS = 4


def parse_admin_ids(raw: str | None) -> frozenset[int]:
    ids: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return frozenset(ids)


def _retry_seconds(exc: RetryAfter) -> float:
    with warnings.catch_warnings():  # int today, timedelta in the next PTB major: accept both
        warnings.simplefilter("ignore", PTBDeprecationWarning)
        value: Any = exc.retry_after
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


class TelegramSender:
    def __init__(self, bot: Any, chat_id: str) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def send(self, text: str) -> None:
        plain = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=strip_tags(text) if plain else text,
                    parse_mode=None if plain else ParseMode.HTML,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return
            except RetryAfter as exc:  # flood control: wait exactly as long as Telegram asks
                await asyncio.sleep(_retry_seconds(exc) + 0.5)
            except BadRequest as exc:
                if plain or "parse" not in str(exc).lower():
                    raise
                log.warning("telegram.html_rejected", error=str(exc)[:200])
                plain = True  # resend once without markup rather than lose the message
            except (TimedOut, NetworkError):
                if attempt == MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(2.0 * attempt)
        raise NetworkError("telegram: gave up after retries")


Reply = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]


def _guarded(commands: BotCommands, name: str) -> Reply:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        message = update.effective_message
        if message is None:
            return
        user_id = user.id if user else None
        chat_id = chat.id if chat else None
        if not commands.authorized(user_id, chat_id):
            log.warning("telegram.unauthorized", command=name, user_id=user_id, chat_id=chat_id)
            return
        text = await commands.run(name, list(context.args or []), user_id=user_id)
        for chunk in split_message(text):
            await message.reply_text(
                chunk,
                parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )

    return handle


def build_application(token: str) -> Application[Any, Any, Any, Any, Any, Any]:
    return ApplicationBuilder().token(token).build()


def add_commands(app: Application[Any, Any, Any, Any, Any, Any], commands: BotCommands) -> None:
    for name in commands.names:
        app.add_handler(CommandHandler(name, _guarded(commands, name)))

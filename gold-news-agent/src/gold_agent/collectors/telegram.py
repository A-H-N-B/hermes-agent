"""Telegram squawk channels via Telethon (push, not polling).

The session file is a full login for the reading account: keep it out of git and images, chmod 600.
"""

# pyright: basic

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from gold_agent.collectors.base import Emit
from gold_agent.config import Source
from gold_agent.core.clock import Clock
from gold_agent.ops.health import HealthTracker
from gold_agent.processing.normalizer import Normalizer

log = structlog.get_logger(__name__)


def message_url(username: str | None, message_id: int) -> str | None:
    return f"https://t.me/{username}/{message_id}" if username else None


def message_external_id(chat_id: int, message_id: int, edit_date: datetime | None) -> str:
    base = f"{chat_id}:{message_id}"
    return f"{base}:edit:{int(edit_date.timestamp())}" if edit_date else base


def as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


class TelegramCollector:
    def __init__(
        self,
        source: Source,
        *,
        api_id: int,
        api_hash: str,
        session_path: Path,
        normalizer: Normalizer,
        emit: Emit,
        health: HealthTracker,
        clock: Clock,
    ) -> None:
        self.source = source
        self.name = f"telegram:{source.id}"
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_path = session_path
        self._normalizer = normalizer
        self._emit = emit
        self._health = health
        self._clock = clock

    async def _handle(self, event: Any, *, edited: bool) -> None:
        message = event.message
        text = message.message or ""
        if not text.strip():
            return
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        item = self._normalizer.from_text(
            self.source,
            external_id=message_external_id(
                event.chat_id, message.id, as_utc(message.edit_date) if edited else None
            ),
            text=text,
            url=message_url(username, message.id),
            published_at=as_utc(message.edit_date if edited else message.date),
            first_seen_at=self._clock.now(),
        )
        if item is not None:
            await self._emit(item)
            self._health.success(1)

    async def run(self, stop: asyncio.Event) -> None:
        from telethon import TelegramClient, events

        client: Any = TelegramClient(
            str(self._session_path), self._api_id, self._api_hash, auto_reconnect=True
        )
        await client.connect()
        try:
            if not await client.is_user_authorized():
                self._health.error(
                    "sesi Telegram belum login: jalankan `gold-agent telegram-login`"
                )
                raise RuntimeError("telegram session is not authorised")
            chats = [await client.get_input_entity(name) for name in self.source.channels]

            async def on_new(event: Any) -> None:
                await self._handle(event, edited=False)

            async def on_edit(event: Any) -> None:
                await self._handle(event, edited=True)

            client.add_event_handler(on_new, events.NewMessage(chats=chats))
            client.add_event_handler(on_edit, events.MessageEdited(chats=chats))
            self._health.success(0)
            log.info("telegram.listening", channels=list(self.source.channels))
            disconnected = asyncio.ensure_future(client.run_until_disconnected())
            stopping = asyncio.ensure_future(stop.wait())
            done, pending = await asyncio.wait(
                {disconnected, stopping}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if disconnected in done and not stop.is_set():
                raise ConnectionError("telegram disconnected")
        finally:
            await client.disconnect()


async def interactive_login(api_id: int, api_hash: str, session_path: Path) -> None:
    """One-time login for the reading account (asks for phone + code on the terminal)."""
    from telethon import TelegramClient

    session_path.parent.mkdir(parents=True, exist_ok=True)
    client: Any = TelegramClient(str(session_path), api_id, api_hash)
    await client.start()
    me = await client.get_me()
    await client.disconnect()
    for candidate in session_path.parent.glob(f"{session_path.name}*"):
        os.chmod(candidate, 0o600)
    print(f"Login berhasil sebagai {getattr(me, 'username', None) or getattr(me, 'id', '?')}.")

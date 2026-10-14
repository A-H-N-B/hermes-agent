"""Notifier service: bus events -> notification policy -> dispatcher -> Telegram, plus the bot's
commands. Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID it still records every notification (the
dashboard shows them) and says so once in the log."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import structlog

from gold_agent.core.app import AppContext
from gold_agent.core.bus import (
    STREAM_ALERTS,
    STREAM_ANALYSES,
    STREAM_EVENTS,
    STREAM_NOTIFY,
    STREAM_OUTLOOK,
    STREAM_PLAYBOOKS,
    STREAM_RELEASES,
    STREAM_SIGNALS,
    Handler,
    Message,
    StartAt,
    consume,
)
from gold_agent.models import (
    Alert,
    Analysis,
    CalendarEvent,
    Notification,
    Outlook,
    Playbook,
    ReleaseReading,
    Signal,
)
from gold_agent.notify.commands import BotCommands
from gold_agent.notify.dispatcher import Dispatcher, Sender
from gold_agent.notify.policy import NotificationPolicy
from gold_agent.notify.telegram import (
    TelegramSender,
    add_commands,
    build_application,
    parse_admin_ids,
)

log = structlog.get_logger(__name__)

FLUSH_SECONDS = 15.0


class NotifierService:
    name = "notifier"

    def __init__(self, ctx: AppContext, *, sender: Sender | None = None, poll: bool = True) -> None:
        self._ctx = ctx
        token = ctx.env.get("TELEGRAM_BOT_TOKEN")
        chat_id = ctx.env.get("TELEGRAM_CHAT_ID")
        self._poll = poll
        self._app: Any = build_application(token) if token else None
        if sender is None and self._app is not None and chat_id:
            sender = TelegramSender(self._app.bot, chat_id)
        self.dispatcher = Dispatcher(
            ctx.settings, repo=ctx.notify, ops=ctx.ops, clock=ctx.clock, sender=sender
        )
        self.policy = NotificationPolicy(ctx.settings)
        self.commands = BotCommands(
            ctx,
            self.dispatcher,
            admin_ids=parse_admin_ids(ctx.env.get("TELEGRAM_ADMIN_USER_IDS")),
            chat_id=chat_id,
        )
        if self._app is not None:
            add_commands(self._app, self.commands)
        self._events: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
            "signal.replaced": self._on_replaced,
        }
        if sender is None:
            log.warning(
                "notifier.no_telegram",
                hint="set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env; recording only",
            )

    async def _next_event(self) -> CalendarEvent | None:
        now = self._ctx.clock.now()
        events = await self._ctx.calendar.between(now, now + timedelta(hours=24))
        return next((e for e in events if e.impact == "high" and not e.all_day), None)

    async def _submit(self, notification: Notification | None) -> None:
        if notification is not None:
            outcome = await self.dispatcher.submit(notification)
            log.info("notify.submitted", kind=notification.kind, outcome=outcome)

    # ------------------------------------------------------------------ handlers

    async def handle_signal(self, message: Message) -> None:
        signal = Signal.model_validate(message.data)
        now = self._ctx.clock.now()
        await self._submit(
            self.policy.for_signal(signal, now=now, next_event=await self._next_event())
        )

    async def handle_release(self, message: Message) -> None:
        release = ReleaseReading.model_validate(message.data)
        await self._submit(self.policy.for_release(release, now=self._ctx.clock.now()))

    async def handle_analysis(self, message: Message) -> None:
        analysis = Analysis.model_validate(message.data)
        await self._submit(self.policy.for_analysis(analysis, now=self._ctx.clock.now()))

    async def handle_alert(self, message: Message) -> None:
        alert = Alert.model_validate(message.data)
        await self._submit(self.policy.for_alert(alert, now=self._ctx.clock.now()))

    async def handle_outlook(self, message: Message) -> None:
        outlook = Outlook.model_validate(message.data)
        await self._submit(self.policy.for_outlook(outlook, now=self._ctx.clock.now()))

    async def handle_playbook(self, message: Message) -> None:
        playbook = Playbook.model_validate(message.data)
        await self._submit(self.policy.for_playbook(playbook, now=self._ctx.clock.now()))

    async def handle_outbox(self, message: Message) -> None:
        """Ready-made notifications from other services (reports, briefs)."""
        await self._submit(Notification.model_validate(message.data))

    async def handle_event(self, message: Message) -> None:
        handler = self._events.get(str(message.data.get("type")))
        if handler is not None:
            await handler(message.data)

    async def _on_replaced(self, data: dict[str, Any]) -> None:
        old = await self._ctx.signals.get_signal(str(data.get("signal_id")))
        if old is None:
            return
        new = await self._ctx.signals.get_signal(str(data.get("by")))
        await self._submit(
            self.policy.for_replaced(
                old,
                new_id=str(data.get("by")),
                new_action=new.action if new else "-",
                now=self._ctx.clock.now(),
            )
        )

    # ------------------------------------------------------------------ run

    async def _flush_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.dispatcher.flush()
            except Exception:
                log.exception("notify.flush_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=FLUSH_SECONDS)

    async def _start_bot(self) -> None:
        app = self._app
        if app is None:
            return
        await app.initialize()  # also prepares app.bot, which the sender uses
        await app.start()
        if self._poll and app.updater is not None:
            await app.updater.start_polling(drop_pending_updates=True, allowed_updates=["message"])
            log.info("telegram.bot_started", commands=list(self.commands.names))

    async def _stop_bot(self) -> None:
        app = self._app
        if app is None:
            return
        with contextlib.suppress(Exception):
            if app.updater is not None and app.updater.running:
                await app.updater.stop()
            await app.stop()
            await app.shutdown()

    async def run(self, stop: asyncio.Event) -> None:
        await self.dispatcher.load()
        bus = self._ctx.bus
        consumers: list[tuple[str, str, Handler, StartAt]] = [
            (STREAM_SIGNALS, "notifier", self.handle_signal, "earliest"),
            (STREAM_RELEASES, "notifier-releases", self.handle_release, "earliest"),
            (STREAM_ANALYSES, "notifier-news", self.handle_analysis, "earliest"),
            (STREAM_ALERTS, "notifier-alerts", self.handle_alert, "earliest"),
            (STREAM_OUTLOOK, "notifier-outlook", self.handle_outlook, "earliest"),
            (STREAM_PLAYBOOKS, "notifier-playbooks", self.handle_playbook, "earliest"),
            (STREAM_NOTIFY, "notifier-outbox", self.handle_outbox, "earliest"),
            (STREAM_EVENTS, "notifier-events", self.handle_event, "latest"),
        ]
        tasks = [
            asyncio.create_task(
                consume(bus, stream, group, "notifier-1", handler, stop, start=start)
            )
            for stream, group, handler, start in consumers
        ]
        tasks.append(asyncio.create_task(self._flush_loop(stop)))
        try:
            await self._start_bot()
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await self._stop_bot()

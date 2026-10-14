"""Paper tracker (section 17): every signal is filled and managed against live quotes from the
same price source, results are stored, and the engine learns when a signal closes (its slot and
the daily loss limit). After a restart it resumes open positions and replays stored ticks."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import structlog

from gold_agent.core.app import AppContext
from gold_agent.core.bus import (
    STREAM_EVENTS,
    STREAM_NOTIFY,
    STREAM_SIGNALS,
    STREAM_TICKS,
    Handler,
    Message,
    StartAt,
    consume,
)
from gold_agent.evaluation.paper import Quote, apply_quote, close_at_market, open_position
from gold_agent.models import Notification, Signal, SignalOutcome, Tick
from gold_agent.models.common import Priority
from gold_agent.notify import templates
from gold_agent.notify.policy import notification_id
from gold_agent.signals.engine import KV_PAPER_DAILY, wib_day
from gold_agent.timeutils.sessions import WIB

log = structlog.get_logger(__name__)

SAVE_EVERY = timedelta(seconds=60)  # MFE/MAE progress; exits are saved immediately
SWEEP_SECONDS = 30.0


class PaperService:
    name = "paper"

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self.positions: dict[str, tuple[Signal, SignalOutcome]] = {}
        self.pending: dict[str, Signal] = {}
        self.last_quote: dict[str, Quote] = {}
        self._saved_at: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._events: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
            "signal.replaced": self._on_replaced,
        }

    # ------------------------------------------------------------------ restart

    async def load(self) -> None:
        ctx = self._ctx
        now = ctx.clock.now()
        for outcome in await ctx.signals.open_outcomes():
            signal = await ctx.signals.get_signal(outcome.signal_id)
            if signal is not None:
                self.positions[signal.signal_id] = (signal, outcome)
        for signal in await ctx.signals.signals_between(now - timedelta(days=1), now):
            if (
                signal.signal_id not in self.positions
                and await ctx.signals.outcome(signal.signal_id) is None
            ):
                self.pending[signal.signal_id] = signal
        await self.catch_up(now)

    async def catch_up(self, now: datetime) -> None:
        """Apply stored ticks missed while the service was down (stored at a sampled rate)."""
        for signal_id, (signal, outcome) in list(self.positions.items()):
            since = outcome.last_price_at or outcome.opened_at
            ticks = await self._ctx.market.ticks_between(signal.price_source, since, now)
            for tick in ticks:
                if signal_id in self.positions:
                    await self._apply(signal_id, Quote(tick.ts, tick.bid, tick.ask))

    # ------------------------------------------------------------------ handlers

    async def handle_signal(self, message: Message) -> None:
        signal = Signal.model_validate(message.data)
        async with self._lock:
            if signal.signal_id in self.positions or signal.signal_id in self.pending:
                return
            if await self._ctx.signals.outcome(signal.signal_id) is not None:
                return  # redelivered after a restart: already tracked
            self.pending[signal.signal_id] = signal

    async def handle_tick(self, message: Message) -> None:
        tick = Tick.model_validate(message.data)
        quote = Quote(tick.ts, tick.bid, tick.ask)
        async with self._lock:
            self.last_quote[tick.source] = quote
            for signal_id, signal in list(self.pending.items()):
                if signal.price_source == tick.source and tick.ts >= signal.created_at_utc:
                    del self.pending[signal_id]
                    await self._open(signal, quote)
            for signal_id, (signal, _) in list(self.positions.items()):
                if signal.price_source == tick.source:
                    await self._apply(signal_id, quote)

    async def handle_event(self, message: Message) -> None:
        handler = self._events.get(str(message.data.get("type")))
        if handler is not None:
            await handler(message.data)

    async def _on_replaced(self, data: dict[str, Any]) -> None:
        signal_id = str(data.get("signal_id"))
        async with self._lock:
            if signal_id in self.pending:
                signal = self.pending.pop(signal_id)
                await self._unfilled(signal, "dibatalkan sebelum terisi (arah berbalik)")
                return
            entry = self.positions.get(signal_id)
            if entry is None:
                return
            signal, outcome = entry
            quote = self.last_quote.get(signal.price_source)
            if quote is None:
                return
            closed = close_at_market(outcome, signal, quote, self._ctx.settings, "cancelled")
            await self._finish(signal, closed.model_copy(update={"notes": ["arah berbalik"]}))

    # ------------------------------------------------------------------ position life cycle

    async def _open(self, signal: Signal, quote: Quote) -> None:
        outcome = open_position(signal, quote, self._ctx.settings)
        self.positions[signal.signal_id] = (signal, outcome)
        await self._ctx.signals.save_outcome(outcome)
        self._saved_at[signal.signal_id] = quote.ts
        log.info("paper.opened", signal=signal.signal_id, fill=outcome.entry_price)
        await self._apply(signal.signal_id, quote)  # a fill already beyond the stop exits now

    async def _apply(self, signal_id: str, quote: Quote) -> None:
        signal, before = self.positions[signal_id]
        after = apply_quote(before, signal, quote, self._ctx.settings)
        if after.closed_at is not None:
            await self._finish(signal, after)
            return
        self.positions[signal_id] = (signal, after)
        if after.tp1_hit_at is not None and before.tp1_hit_at is None:
            await self._ctx.signals.save_outcome(after)
            self._saved_at[signal_id] = quote.ts
            await self._notify(
                signal, "high", templates.render_paper_tp1(signal, after), f"paper-tp1:{signal_id}"
            )
        elif quote.ts - self._saved_at.get(signal_id, after.opened_at) >= SAVE_EVERY:
            await self._ctx.signals.save_outcome(after)
            self._saved_at[signal_id] = quote.ts

    async def _finish(self, signal: Signal, outcome: SignalOutcome) -> None:
        ctx = self._ctx
        self.positions.pop(signal.signal_id, None)
        self._saved_at.pop(signal.signal_id, None)
        await ctx.signals.save_outcome(outcome)
        await ctx.bus.publish(
            STREAM_EVENTS, {"type": "signal.closed", "signal_id": signal.signal_id}
        )
        log.info(
            "paper.closed", signal=signal.signal_id, status=outcome.status, r=outcome.r_multiple
        )
        await self._notify(
            signal,
            "normal",
            templates.render_paper_close(signal, outcome),
            f"paper-close:{signal.signal_id}",
        )
        await self._publish_daily(outcome.closed_at or ctx.clock.now())

    async def _unfilled(self, signal: Signal, reason: str) -> None:
        """A signal that never got a quote is recorded but never counted as a trade."""
        now = self._ctx.clock.now()
        outcome = SignalOutcome(
            signal_id=signal.signal_id,
            action="BUY" if signal.action == "BUY" else "SELL",
            status="cancelled",
            entry_price=0.0,
            opened_at=now,
            closed_at=now,
            r_multiple=None,
            remaining=0.0,
            price_source=signal.price_source,
            notes=[reason],
        )
        await self._ctx.signals.save_outcome(outcome)
        await self._ctx.bus.publish(
            STREAM_EVENTS, {"type": "signal.closed", "signal_id": signal.signal_id}
        )

    async def _publish_daily(self, moment: datetime) -> None:
        ctx = self._ctx
        local = moment.astimezone(WIB)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        closed = await ctx.signals.outcomes_closed_between(start, start + timedelta(days=1))
        total = round(sum(o.r_multiple or 0.0 for o in closed), 3)
        payload = {"day": wib_day(moment), "r": total, "trades": len(closed)}
        await ctx.ops.kv_set(KV_PAPER_DAILY, payload, ctx.clock.now())
        await ctx.bus.publish(STREAM_EVENTS, {"type": "paper.daily", **payload})

    async def _notify(self, signal: Signal, priority: Priority, text: str, key: str) -> None:
        now = self._ctx.clock.now()
        notification = Notification(
            id=notification_id(key, now.isoformat()),
            kind="signal_update",
            priority=priority,
            text=text,
            created_at=now,
            dedupe_key=key,
            ref_ids=[signal.signal_id],
        )
        await self._ctx.bus.publish(STREAM_NOTIFY, notification)

    # ------------------------------------------------------------------ run

    async def sweep(self) -> None:
        """Signals that never saw a quote before they expired are recorded as unfilled."""
        now = self._ctx.clock.now()
        async with self._lock:
            for signal_id, signal in list(self.pending.items()):
                if now >= signal.valid_until_utc:
                    del self.pending[signal_id]
                    await self._unfilled(signal, "tidak tereksekusi: tidak ada harga dari sumber")

    async def _sweep_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.sweep()
            except Exception:
                log.exception("paper.sweep_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=SWEEP_SECONDS)

    async def run(self, stop: asyncio.Event) -> None:
        await self.load()
        bus = self._ctx.bus
        consumers: list[tuple[str, str, Handler, StartAt]] = [
            (STREAM_SIGNALS, "paper", self.handle_signal, "earliest"),
            (STREAM_TICKS, "paper-ticks", self.handle_tick, "latest"),
            (STREAM_EVENTS, "paper-events", self.handle_event, "latest"),
        ]
        tasks = [
            asyncio.create_task(consume(bus, stream, group, "paper-1", handler, stop, start=start))
            for stream, group, handler, start in consumers
        ]
        tasks.append(asyncio.create_task(self._sweep_loop(stop)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()

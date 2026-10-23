"""Walk-forward accuracy of the forecast per timeframe and session, from the stored history.

The signal service reads it to choose the timeframe (signals/timeframe.py); the dashboard shows
it under the forecast. A rebuild reads 30 days of bars (M1: 7) and the engine's stored scores and
takes seconds per timeframe, so results are cached for REFRESH and the numeric part runs off the
event loop. On the wall clock nobody waits for a rebuild: the last result keeps serving while the
new one is computed in the background (one timeframe at a time), so evaluations and the
dashboard's forecast stay real-time. Replays and tests compute in line, so their results stay
deterministic. One book serves every service of a process (`AccuracyBook.shared`), so the
dashboard reads the signal service's results instead of computing them a second time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from gold_agent.config import TRADE_TIMEFRAMES
from gold_agent.core.app import AppContext
from gold_agent.core.clock import SystemClock
from gold_agent.models import CalendarEvent
from gold_agent.signals.forecast import SPAN
from gold_agent.signals.forecast_score import Held, forecast_accuracy
from gold_agent.signals.meta import train_meta
from gold_agent.signals.setups import replay_setups, tally
from gold_agent.timeutils.sessions import SessionClock

JSON = dict[str, Any]
log = structlog.get_logger(__name__)
REFRESH = timedelta(hours=1)
RETRY = timedelta(minutes=5)  # after a failed background rebuild
WARMUP_BARS = 460  # EMA200 has forgotten its seed by the first measured bar
SCORED = (*TRADE_TIMEFRAMES, "H4", "D1")  # W1/MN have too few bars in 30 days


def days_for(tf: str) -> int:
    return 7 if tf == "M1" else 30


@dataclass(frozen=True)
class _Shared:
    at: datetime
    scores: list[tuple[datetime, float]]
    held: list[Held]
    events: list[CalendarEvent]


class AccuracyBook:
    def __init__(
        self, ctx: AppContext, *, learn: bool = True, background: bool | None = None
    ) -> None:
        """`learn`: also train the meta-labeling model (the signal service; the dashboard only
        shows accuracy). `background`: rebuild without making callers wait (default: on the
        wall clock only)."""
        self._ctx = ctx
        self._sessions = SessionClock(ctx.settings.sessions)
        self._shared: _Shared | None = None
        self._results: dict[str, tuple[datetime, JSON]] = {}
        self._lock = asyncio.Lock()
        self._learn = learn
        self._background = isinstance(ctx.clock, SystemClock) if background is None else background
        self._pending: dict[str, asyncio.Task[JSON]] = {}
        self._failed: dict[str, datetime] = {}

    @classmethod
    def shared(cls, ctx: AppContext, *, learn: bool = False) -> AccuracyBook:
        """The process's book; it learns once any of its users does."""
        book = ctx.accuracy_book
        if book is None:
            book = ctx.accuracy_book = cls(ctx, learn=learn)
        book._learn = book._learn or learn
        return book

    def _fresh(self, tf: str, now: datetime) -> JSON | None:
        cached = self._results.get(tf)
        if cached is None or now - cached[0] >= REFRESH:
            return None
        if self._learn and tf in TRADE_TIMEFRAMES and "meta" not in cached[1]:
            return None  # built before a learning user joined
        return cached[1]

    async def _load_shared(self, now: datetime) -> _Shared:
        if self._shared is not None and now - self._shared.at < REFRESH:
            return self._shared
        ctx = self._ctx
        start = now - timedelta(days=30)
        signals = await ctx.signals.signals_between(start - timedelta(days=2), now)
        closed = {o.signal_id: o.closed_at for o in await ctx.signals.outcomes_between(start, now)}
        held: list[Held] = sorted(
            (
                s.created_at_utc,
                min(s.valid_until_utc, closed.get(s.signal_id) or s.valid_until_utc),
                1.0 if s.action == "BUY" else -1.0,
            )
            for s in signals
        )
        self._shared = _Shared(
            at=now,
            scores=await ctx.signals.scores_between(start - timedelta(minutes=10), now),
            held=held,
            events=await ctx.calendar.between(start - timedelta(hours=1), now),
        )
        return self._shared

    async def accuracy(self, tf: str, *, source: str | None) -> JSON | None:
        """One timeframe's walk-forward result (total, per session, news/chart parts). In the
        background mode a stale or missing result starts a rebuild and returns what there is
        (None before the first one)."""
        if tf not in SCORED:
            return None
        now = self._ctx.clock.now()
        fresh = self._fresh(tf, now)
        if fresh is not None:
            return fresh
        cached = self._results.get(tf)
        if not self._background:
            return await self._rebuild(tf, source)
        failed = self._failed.get(tf)
        if tf not in self._pending and (failed is None or now - failed >= RETRY):
            task = asyncio.create_task(self._rebuild(tf, source))
            self._pending[tf] = task
            task.add_done_callback(lambda done, tf=tf: self._settle(tf, done))
        return cached[1] if cached else None

    def _settle(self, tf: str, task: asyncio.Task[JSON]) -> None:
        self._pending.pop(tf, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._failed[tf] = self._ctx.clock.now()
            log.warning("accuracy.rebuild_failed", tf=tf, error=repr(error)[:200])

    async def _rebuild(self, tf: str, source: str | None) -> JSON:
        async with self._lock:  # one timeframe at a time: bounded CPU next to the live loop
            now = self._ctx.clock.now()
            fresh = self._fresh(tf, now)
            if fresh is not None:
                return fresh
            shared = await self._load_shared(now)
            start = now - timedelta(days=days_for(tf))
            bars = await self._ctx.market.candles(
                tf, start - SPAN[tf] * WARMUP_BARS, now, source=source
            )
            settings = self._ctx.settings
            engine = settings.signal_engine
            result = await asyncio.to_thread(
                forecast_accuracy,
                tf=tf,
                bars=bars,
                scores=shared.scores,
                held=shared.held,
                events=shared.events,
                threshold=engine.entry_threshold,
                confirm=timedelta(seconds=settings.trading.post_release_confirm_seconds),
                now=now,
                start=start,
                session_of=self._sessions.session,
            )
            setups = await asyncio.to_thread(
                replay_setups,
                bars=bars,
                scores=shared.scores,
                held=shared.held,
                threshold=engine.entry_threshold,
                sl_atr=engine.levels.sl_atr_multiple,
                tp1_r=engine.levels.tp_r_multiples[0],
                validity_bars=engine.validity_bars,
                now=now,
                start=start,
            )
            result["setups"] = tally(setups, self._sessions.session)
            if self._learn and tf in TRADE_TIMEFRAMES:  # the charts a signal can trade on
                result["meta"] = await asyncio.to_thread(
                    train_meta, tf, bars, setups, settings.market.candle_buffer
                )
            result["days"] = days_for(tf)
            self._results[tf] = (now, result)
            return result

    async def table(
        self, *, source: str | None, timeframes: Sequence[str] = TRADE_TIMEFRAMES
    ) -> dict[str, dict[str, JSON]]:
        """timeframe -> session -> tally, what the timeframe choice reads. Each session's tally
        also carries how the timeframe's past setups ended and the meta-labeling model learnt
        from them (all sessions: signals/odds.py, signals/meta.py)."""
        table: dict[str, dict[str, JSON]] = {}
        for tf in timeframes:
            result = await self.accuracy(tf, source=source) or {}
            extra = {key: result[key] for key in ("setups", "meta") if result.get(key)}
            table[tf] = {
                session: counts | extra
                for session, counts in (result.get("by_session") or {}).items()
            }
        return table

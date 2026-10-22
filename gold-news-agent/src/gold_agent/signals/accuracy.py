"""Walk-forward accuracy of the forecast per timeframe and session, from the stored history.

The signal service reads it to choose the timeframe (signals/timeframe.py); the dashboard shows
it under the forecast. A rebuild reads 30 days of bars (M1: 7) and the engine's stored scores,
so results are cached for REFRESH and the numeric part runs off the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from gold_agent.config import TRADE_TIMEFRAMES
from gold_agent.core.app import AppContext
from gold_agent.models import CalendarEvent
from gold_agent.signals.forecast import SPAN
from gold_agent.signals.forecast_score import Held, forecast_accuracy
from gold_agent.signals.setups import setup_outcomes
from gold_agent.timeutils.sessions import SessionClock

JSON = dict[str, Any]
REFRESH = timedelta(hours=1)
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
    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._sessions = SessionClock(ctx.settings.sessions)
        self._shared: _Shared | None = None
        self._results: dict[str, tuple[datetime, JSON]] = {}
        self._lock = asyncio.Lock()

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
        """One timeframe's walk-forward result (total, per session, news/chart parts)."""
        if tf not in SCORED:
            return None
        now = self._ctx.clock.now()
        async with self._lock:
            cached = self._results.get(tf)
            if cached is not None and now - cached[0] < REFRESH:
                return cached[1]
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
            result["setups"] = await asyncio.to_thread(
                setup_outcomes,
                bars=bars,
                scores=shared.scores,
                held=shared.held,
                threshold=engine.entry_threshold,
                sl_atr=engine.levels.sl_atr_multiple,
                tp1_r=engine.levels.tp_r_multiples[0],
                validity_bars=engine.validity_bars,
                now=now,
                start=start,
                session_of=self._sessions.session,
            )
            result["days"] = days_for(tf)
            self._results[tf] = (now, result)
            return result

    async def table(
        self, *, source: str | None, timeframes: Sequence[str] = TRADE_TIMEFRAMES
    ) -> dict[str, dict[str, JSON]]:
        """timeframe -> session -> tally, what the timeframe choice reads. Each session's tally
        also carries how the timeframe's past setups ended (all sessions: signals/odds.py)."""
        table: dict[str, dict[str, JSON]] = {}
        for tf in timeframes:
            result = await self.accuracy(tf, source=source) or {}
            setups = result.get("setups")
            table[tf] = {
                session: {**tally, "setups": setups} if setups else tally
                for session, tally in (result.get("by_session") or {}).items()
            }
        return table

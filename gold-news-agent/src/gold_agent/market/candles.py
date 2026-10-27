"""Candles M1..D1 built from ticks (mid price). H4/D1 follow the gold trading day (17:00 NY)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe
from gold_agent.timeutils.sessions import SessionClock

TF_MINUTES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}
ALL_TIMEFRAMES: tuple[Timeframe, ...] = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


def bucket(tf: Timeframe, ts: datetime, sessions: SessionClock) -> tuple[datetime, datetime]:
    """(open_time, close_time) of the candle containing `ts`."""
    if tf in ("H4", "D1"):
        day_start = sessions.trading_day_start(ts)
        # 25h ahead always lands in the next trading day, even on 23h/25h DST days.
        day_end = sessions.trading_day_start(day_start + timedelta(hours=25))
        if tf == "D1":
            return day_start, day_end
        index = int((ts - day_start).total_seconds() // (4 * 3600))
        start = day_start + timedelta(hours=4 * index)
        return start, min(start + timedelta(hours=4), day_end)
    seconds = TF_MINUTES[tf] * 60
    epoch = ts.timestamp()
    start = datetime.fromtimestamp(epoch - epoch % seconds, ts.tzinfo)
    return start, start + timedelta(seconds=seconds)


def aggregate(candles: Iterable[Candle], tf: Timeframe, sessions: SessionClock) -> list[Candle]:
    """Roll lower-timeframe candles up into `tf` (e.g. H1 -> H4/D1 with trading-day anchors)."""
    result: list[Candle] = []
    for candle in sorted(candles, key=lambda c: c.open_time):
        open_time, close_time = bucket(tf, candle.open_time, sessions)
        if result and result[-1].open_time == open_time:
            last = result[-1]
            result[-1] = last.model_copy(
                update={
                    "high": max(last.high, candle.high),
                    "low": min(last.low, candle.low),
                    "close": candle.close,
                    "volume": last.volume + candle.volume,
                    "ticks": last.ticks + candle.ticks,
                    "complete": candle.complete and candle.close_time >= close_time,
                }
            )
        else:
            result.append(
                Candle(
                    source=candle.source,
                    tf=tf,
                    open_time=open_time,
                    close_time=close_time,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    ticks=candle.ticks,
                    complete=candle.complete and candle.close_time >= close_time,
                )
            )
    return result


def shifted(candle: Candle, delta: float) -> Candle:
    """The same bar `delta` higher (a feed's prices re-based onto the broker's)."""
    return candle.model_copy(
        update={
            "open": candle.open + delta,
            "high": candle.high + delta,
            "low": candle.low + delta,
            "close": candle.close + delta,
        }
    )


class CandleBuilder:
    def __init__(
        self,
        source: str,
        sessions: SessionClock,
        *,
        timeframes: Sequence[Timeframe] = ALL_TIMEFRAMES,
        buffer: int = 600,
    ) -> None:
        self.source = source
        self._sessions = sessions
        self.timeframes = tuple(timeframes)
        self._history: dict[str, deque[Candle]] = {tf: deque(maxlen=buffer) for tf in timeframes}
        self._current: dict[str, Candle | None] = dict.fromkeys(timeframes)

    def reset(self) -> None:
        """Forget every bar (the feed switched instrument; history is loaded again)."""
        for tf in self.timeframes:
            self._history[tf].clear()
            self._current[tf] = None

    def shift(self, delta: float) -> None:
        """Move every bar by `delta`, so bars before and after a re-basing stay one series."""
        for tf in self.timeframes:
            history = self._history[tf]
            moved = [shifted(candle, delta) for candle in history]
            history.clear()
            history.extend(moved)
            current = self._current[tf]
            if current is not None:
                self._current[tf] = shifted(current, delta)

    def seed(self, tf: Timeframe, candles: Iterable[Candle]) -> None:
        """Load history. A trailing incomplete candle becomes the live candle."""
        ordered = sorted(candles, key=lambda c: c.open_time)
        history = self._history[tf]
        known = {c.open_time for c in history}
        for candle in ordered:
            if not candle.complete:
                self._current[tf] = candle
                continue
            if candle.open_time not in known:
                history.append(candle)
                known.add(candle.open_time)

    def update(self, tick: Tick) -> list[Candle]:
        """Apply a tick; returns candles that this tick completed."""
        completed: list[Candle] = []
        price = tick.mid
        for tf in self.timeframes:
            open_time, close_time = bucket(tf, tick.ts, self._sessions)
            current = self._current[tf]
            if current is not None and open_time < current.open_time:
                continue  # late tick for an already-closed candle
            if current is None or current.open_time != open_time:
                if current is not None:
                    done = current.model_copy(update={"complete": True})
                    self._history[tf].append(done)
                    completed.append(done)
                self._current[tf] = Candle(
                    source=self.source,
                    tf=tf,
                    open_time=open_time,
                    close_time=close_time,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=tick.volume,
                    ticks=1,
                    complete=False,
                )
            else:
                self._current[tf] = current.model_copy(
                    update={
                        "high": max(current.high, price),
                        "low": min(current.low, price),
                        "close": price,
                        "volume": current.volume + tick.volume,
                        "ticks": current.ticks + 1,
                    }
                )
        return completed

    def close_due(self, now: datetime) -> list[Candle]:
        """Finalise live candles whose period ended without a new tick (quiet markets)."""
        completed: list[Candle] = []
        for tf in self.timeframes:
            current = self._current[tf]
            if current is not None and current.close_time <= now:
                done = current.model_copy(update={"complete": True})
                self._history[tf].append(done)
                completed.append(done)
                self._current[tf] = None
        return completed

    def candles(self, tf: Timeframe, *, include_current: bool = True) -> list[Candle]:
        result = list(self._history[tf])
        current = self._current[tf]
        if include_current and current is not None:
            result.append(current)
        return result

    def current(self, tf: Timeframe) -> Candle | None:
        return self._current[tf]

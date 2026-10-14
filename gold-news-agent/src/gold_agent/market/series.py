"""A price series for point-in-time lookups (signal scoring, paper fills, event studies)."""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from datetime import datetime, timedelta

from gold_agent.models import Candle

MAX_GAP = timedelta(minutes=5)  # older than this before `t` means "no price at t"


class PriceSeries:
    """Time-ordered prices answering "what was the price at t?" without looking ahead."""

    def __init__(self, *, keep: timedelta | None = None, max_gap: timedelta = MAX_GAP) -> None:
        self._keep = keep
        self._max_gap = max_gap
        self._times: list[datetime] = []
        self._prices: list[float] = []

    def __len__(self) -> int:
        return len(self._times)

    def add(self, ts: datetime, price: float) -> None:
        if self._times and ts <= self._times[-1]:
            index = bisect.bisect_left(self._times, ts)
            if index < len(self._times) and self._times[index] == ts:
                self._prices[index] = price
                return
            self._times.insert(index, ts)
            self._prices.insert(index, price)
        else:
            self._times.append(ts)
            self._prices.append(price)
        if self._keep is not None:
            drop = bisect.bisect_left(self._times, self._times[-1] - self._keep)
            if drop:
                del self._times[:drop]
                del self._prices[:drop]

    def at(self, moment: datetime) -> float | None:
        """Last price at or before `moment`; None before the buffer or across a data gap."""
        index = bisect.bisect_right(self._times, moment) - 1
        if index < 0 or moment - self._times[index] > self._max_gap:
            return None
        return self._prices[index]

    @classmethod
    def from_candles(
        cls, candles: Iterable[Candle], *, max_gap: timedelta = MAX_GAP
    ) -> PriceSeries:
        """Bar closes stamped at the bar's close time: the price known when the bar finished."""
        series = cls(max_gap=max_gap)
        for candle in sorted(candles, key=lambda c: c.close_time):
            series.add(candle.close_time, candle.close)
        return series

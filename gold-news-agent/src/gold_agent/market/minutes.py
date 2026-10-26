"""A minute series (the last value seen in each minute, a few days long) with vectorised reads.

The dollar tracker and every related market keep one, and each context (every 5 s) asks for
moves, z-scores and the value some minutes ago. Done with Python loops over thousands of points
and the exact-arithmetic `statistics` module, that cost a few hundred milliseconds per context on
the event loop, so prices and the dashboard stalled. Here the points are mirrored in numpy arrays
rebuilt only when a minute is added (a new value inside the same minute patches the last one).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray


def _epoch_minute(ts: datetime) -> int:
    return int(ts.timestamp() // 60)


class MinuteSeries:
    def __init__(self, days: int) -> None:
        self.points: deque[tuple[datetime, float]] = deque(maxlen=days * 24 * 60)
        self._key: tuple[int, datetime, datetime] | None = None
        self._minutes: NDArray[np.int64] = np.zeros(0, dtype=np.int64)
        self._logs: NDArray[np.float64] = np.zeros(0)

    def update(self, ts: datetime, value: float) -> None:
        if not math.isfinite(value) or value <= 0:
            return
        minute = ts.replace(second=0, microsecond=0)
        if self.points and self.points[-1][0] == minute:
            self.points[-1] = (minute, value)
        elif not self.points or minute > self.points[-1][0]:
            self.points.append((minute, value))

    def seed(self, points: Iterable[tuple[datetime, float]]) -> None:
        """Older history (a broker's or the stored bars): only points older than what we have."""
        first = self.points[0][0] if self.points else None
        older = sorted(p for p in points if first is None or p[0] < first)
        merged = [*older, *self.points]
        self.points.clear()
        self._key = None
        for ts, value in merged:
            self.update(ts, value)

    def shift(self, delta: float) -> None:
        """Move every value by `delta` (the feed was re-based onto the broker's prices)."""
        moved = [(ts, value + delta) for ts, value in self.points]
        self.points.clear()
        self.points.extend(moved)
        self._key = None

    @property
    def latest(self) -> float | None:
        return self.points[-1][1] if self.points else None

    def arrays(self) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
        """(epoch minutes, log values), oldest first."""
        if not self.points:
            return np.zeros(0, dtype=np.int64), np.zeros(0)
        key = (len(self.points), self.points[0][0], self.points[-1][0])
        if key != self._key:
            self._key = key
            self._minutes = np.fromiter(
                (_epoch_minute(ts) for ts, _ in self.points), np.int64, len(self.points)
            )
            self._logs = np.log(np.fromiter((v for _, v in self.points), np.float64))
        else:
            self._logs[-1] = math.log(self.points[-1][1])  # the current minute moved on
        return self._minutes, self._logs

    def _log_at(self, moment: datetime) -> float | None:
        minutes, logs = self.arrays()
        index = int(np.searchsorted(minutes, _epoch_minute(moment), side="right")) - 1
        return float(logs[index]) if index >= 0 else None

    def value_at(self, moment: datetime) -> float | None:
        log = self._log_at(moment)
        return math.exp(log) if log is not None else None

    def change_pct(self, now: datetime, minutes: int) -> float | None:
        current, previous = self._log_at(now), self._log_at(now - timedelta(minutes=minutes))
        if current is None or previous is None:
            return None
        return 100 * math.expm1(current - previous)

    def zscore(self, now: datetime, minutes: int, min_samples: int = 60) -> float | None:
        """This move against every move of the same length (in points, at most twice as long in
        time) in the series."""
        current, previous = self._log_at(now), self._log_at(now - timedelta(minutes=minutes))
        if current is None or previous is None:
            return None
        stamps, logs = self.arrays()
        if len(logs) <= minutes:
            return None
        changes = (logs[minutes:] - logs[:-minutes])[
            (stamps[minutes:] - stamps[:-minutes]) <= 2 * minutes
        ]
        if len(changes) < min_samples:
            return None
        sigma = float(changes.std())
        if sigma == 0:
            return 0.0
        return (current - previous - float(changes.mean())) / sigma

    def buckets(self, step_minutes: int) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
        """(bucket number, log value) of the last value in each `step_minutes` bucket."""
        stamps, logs = self.arrays()
        if not len(stamps):
            return stamps, logs
        ids = stamps // step_minutes
        last = np.append(np.flatnonzero(np.diff(ids)), len(ids) - 1)
        return ids[last], logs[last]

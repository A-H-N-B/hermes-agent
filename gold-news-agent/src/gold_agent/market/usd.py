"""USD strength (DXY, synthetic DXY or an EURUSD proxy) and z-scored momentum (section 9)."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timedelta
from statistics import fmean, pstdev

# ICE U.S. Dollar Index definition (geometric weights of six USD pairs).
DXY_CONSTANT = 50.14348112
DXY_WEIGHTS: dict[str, float] = {
    "EURUSD": -0.576,
    "USDJPY": 0.136,
    "GBPUSD": -0.119,
    "USDCAD": 0.091,
    "USDSEK": 0.042,
    "USDCHF": 0.036,
}


def synthetic_dxy(quotes: Mapping[str, float]) -> float | None:
    """DXY from the six component pairs; None unless all are present and positive."""
    if any(quotes.get(pair, 0.0) <= 0 for pair in DXY_WEIGHTS):
        return None
    value = DXY_CONSTANT
    for pair, weight in DXY_WEIGHTS.items():
        value *= quotes[pair] ** weight
    return value


class UsdTracker:
    """Per-minute USD strength series; z-scores of 15m/1h log changes vs their own history."""

    def __init__(self, *, source: str, history_days: int = 5, min_samples: int = 60) -> None:
        self.source = source
        self._points: deque[tuple[datetime, float]] = deque(maxlen=history_days * 24 * 60)
        self._min_samples = min_samples

    def update(self, ts: datetime, value: float) -> None:
        if value <= 0:
            return
        minute = ts.replace(second=0, microsecond=0)
        if self._points and self._points[-1][0] == minute:
            self._points[-1] = (minute, value)
        elif not self._points or minute > self._points[-1][0]:
            self._points.append((minute, value))

    def update_eurusd(self, ts: datetime, eurusd: float) -> None:
        """EURUSD proxy: USD strength rises as EURUSD falls."""
        if eurusd > 0:
            self.update(ts, 1.0 / eurusd)

    @property
    def latest(self) -> float | None:
        return self._points[-1][1] if self._points else None

    def _value_at(self, moment: datetime) -> float | None:
        for ts, value in reversed(self._points):
            if ts <= moment:
                return value
        return None

    def zscore(self, now: datetime, minutes: int) -> float | None:
        current = self._value_at(now)
        previous = self._value_at(now - timedelta(minutes=minutes))
        if current is None or previous is None:
            return None
        points = list(self._points)
        lag = minutes
        changes = [
            math.log(points[i][1] / points[i - lag][1])
            for i in range(lag, len(points))
            if points[i][0] - points[i - lag][0] <= timedelta(minutes=lag * 2)
        ]
        if len(changes) < self._min_samples:
            return None
        sigma = pstdev(changes)
        if sigma == 0:
            return 0.0
        return (math.log(current / previous) - fmean(changes)) / sigma

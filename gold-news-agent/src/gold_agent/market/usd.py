"""USD strength (DXY, synthetic DXY or an EURUSD proxy) and z-scored momentum (section 9)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from gold_agent.market.minutes import MinuteSeries

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
        self._series = MinuteSeries(history_days)
        self._min_samples = min_samples

    def update(self, ts: datetime, value: float) -> None:
        self._series.update(ts, value)

    def update_eurusd(self, ts: datetime, eurusd: float) -> None:
        """EURUSD proxy: USD strength rises as EURUSD falls."""
        if eurusd > 0:
            self.update(ts, 1.0 / eurusd)

    @property
    def latest(self) -> float | None:
        return self._series.latest

    def zscore(self, now: datetime, minutes: int) -> float | None:
        return self._series.zscore(now, minutes, self._min_samples)

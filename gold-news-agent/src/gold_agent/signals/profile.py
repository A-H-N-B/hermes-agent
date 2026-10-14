"""Hourly statistical profile (11.2): range, direction and spread per hour of day.

Buckets exist per WIB hour and per local London / New York hour. Session-anchored hours keep
their meaning across DST (US data is always 08:30 New York), which a fixed WIB hour does not, so
lookups prefer the clock of the session that drives that hour and fall back to the WIB hour.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from gold_agent.models import Candle

Basis = Literal["wib_hour", "london_hour", "ny_hour"]
BASIS_ZONES: dict[Basis, ZoneInfo] = {
    "wib_hour": ZoneInfo("Asia/Jakarta"),
    "london_hour": ZoneInfo("Europe/London"),
    "ny_hour": ZoneInfo("America/New_York"),
}
SESSION_BASIS: dict[str, Basis] = {
    "asia": "wib_hour",
    "london": "london_hour",
    "overlap": "ny_hour",
    "new_york": "ny_hour",
}


@dataclass(frozen=True)
class HourStats:
    basis: Basis
    bucket: int  # local hour 0-23 in the basis' time zone
    samples: int
    mean_range: float | None
    median_range: float | None
    std_range: float | None
    pct_up: float | None  # share of bars that closed above their open
    avg_spread: float | None = None

    @property
    def key(self) -> str:
        return f"{self.basis}:{self.bucket}"


def compute_profile(
    candles: Iterable[Candle], spreads: Mapping[int, float] | None = None
) -> list[HourStats]:
    """H1 bars -> stats per bucket. `spreads` maps WIB hour -> average spread when known."""
    ranges: dict[tuple[Basis, int], list[float]] = defaultdict(list)
    ups: dict[tuple[Basis, int], int] = defaultdict(int)
    for candle in candles:
        if candle.tf != "H1" or not candle.complete:
            continue
        span = candle.high - candle.low
        for basis, zone in BASIS_ZONES.items():
            key = (basis, candle.open_time.astimezone(zone).hour)
            ranges[key].append(span)
            ups[key] += candle.close > candle.open
    result: list[HourStats] = []
    for (basis, bucket), values in sorted(ranges.items()):
        result.append(
            HourStats(
                basis=basis,
                bucket=bucket,
                samples=len(values),
                mean_range=round(statistics.fmean(values), 3),
                median_range=round(statistics.median(values), 3),
                std_range=round(statistics.pstdev(values), 3),
                pct_up=round(ups[(basis, bucket)] / len(values), 3),
                avg_spread=(spreads or {}).get(bucket) if basis == "wib_hour" else None,
            )
        )
    return result


class HourlyProfile:
    def __init__(self, stats: Sequence[HourStats] = (), *, min_samples: int = 20) -> None:
        self._stats = {(s.basis, s.bucket): s for s in stats}
        self._min_samples = min_samples

    @property
    def empty(self) -> bool:
        return not any(s.samples >= self._min_samples for s in self._stats.values())

    def _get(self, basis: Basis, moment: datetime) -> HourStats | None:
        stats = self._stats.get((basis, moment.astimezone(BASIS_ZONES[basis]).hour))
        if stats is None or stats.samples < self._min_samples or stats.median_range is None:
            return None
        return stats

    def lookup(self, hour_start: datetime, session: str) -> HourStats | None:
        preferred = SESSION_BASIS.get(session, "wib_hour")
        return self._get(preferred, hour_start) or self._get("wib_hour", hour_start)

    def typical_range(self) -> float | None:
        """Median of the per-WIB-hour medians: the yardstick for 'low / normal / high'."""
        medians = [
            s.median_range
            for (basis, _), s in self._stats.items()
            if basis == "wib_hour" and s.samples >= self._min_samples and s.median_range
        ]
        return statistics.median(medians) if medians else None

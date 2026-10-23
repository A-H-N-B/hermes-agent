"""The minute series behind the dollar tracker and the related markets (market/minutes.py): its
vectorised reads give the same answers as reading the points one by one, and a value that
changes inside the current minute is seen by the very next read."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from gold_agent.market.minutes import MinuteSeries

T0 = datetime(2026, 10, 13, 0, 0, tzinfo=UTC)


def gappy(seed: int = 3, n: int = 3000) -> list[tuple[datetime, float]]:
    """A random walk with missing minutes and a weekend-like hole."""
    rng = np.random.default_rng(seed)
    points, value, minute = [], 2650.0, 0
    for _ in range(n):
        minute += 1 if rng.random() > 0.1 else int(rng.integers(2, 5))
        if 1500 <= minute < 1505:
            minute += 600
        value *= math.exp(rng.normal(0, 3e-4))
        points.append((T0 + timedelta(minutes=minute), value))
    return points


def reference_value(points: list[tuple[datetime, float]], moment: datetime) -> float | None:
    earlier = [value for ts, value in points if ts <= moment]
    return earlier[-1] if earlier else None


def reference_z(points: list[tuple[datetime, float]], now: datetime, lag: int) -> float | None:
    current = reference_value(points, now)
    previous = reference_value(points, now - timedelta(minutes=lag))
    if current is None or previous is None:
        return None
    changes = [
        math.log(points[i][1] / points[i - lag][1])
        for i in range(lag, len(points))
        if points[i][0] - points[i - lag][0] <= timedelta(minutes=2 * lag)
    ]
    mean = sum(changes) / len(changes)
    sigma = math.sqrt(sum((c - mean) ** 2 for c in changes) / len(changes))
    return (math.log(current / previous) - mean) / sigma


def test_reads_match_the_points_one_by_one() -> None:
    points = gappy()
    series = MinuteSeries(days=5)
    for ts, value in points:
        series.update(ts + timedelta(seconds=20), value)
    now = points[-1][0] + timedelta(seconds=30)
    for back in (0, 1, 7, 15, 60, 601, 1440):
        moment = now - timedelta(minutes=back)
        assert series.value_at(moment) == pytest.approx(reference_value(points, moment))
    for lag in (15, 60):
        assert series.zscore(now, lag) == pytest.approx(reference_z(points, now, lag))
        previous = reference_value(points, now - timedelta(minutes=lag))
        assert previous is not None
        expected = 100 * (points[-1][1] / previous - 1)
        assert series.change_pct(now, lag) == pytest.approx(expected)
    assert series.value_at(T0) is None  # before the first point


def test_a_new_value_in_the_current_minute_is_read_at_once() -> None:
    series = MinuteSeries(days=1)
    for i in range(200):
        series.update(T0 + timedelta(minutes=i), 100.0 * (1 + 1e-4 * (i % 3)))
    now = T0 + timedelta(minutes=199, seconds=30)
    before = series.zscore(now, 15)
    series.update(now, 101.0)  # a jump inside the same minute
    assert len(series.points) == 200
    assert series.value_at(now) == pytest.approx(101.0)
    after = series.zscore(now, 15)
    assert before is not None and after is not None and after > before + 3
    later = now + timedelta(minutes=1)
    series.update(later, 101.5)  # and a new minute
    assert series.change_pct(later, 1) == pytest.approx(100 * (101.5 / 101 - 1))

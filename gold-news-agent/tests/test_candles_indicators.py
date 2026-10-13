from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from gold_agent.config import load_settings
from gold_agent.market import indicators as ind
from gold_agent.market.candles import CandleBuilder, aggregate, bucket
from gold_agent.market.levels import asia_range, compute_levels, nearest_above, nearest_below
from gold_agent.market.usd import UsdTracker, synthetic_dxy
from gold_agent.models import Candle, Tick
from gold_agent.timeutils.sessions import SessionClock

SESSIONS = SessionClock(load_settings().sessions)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def tick(ts: datetime, mid: float, spread: float = 0.2, volume: float = 1.0) -> Tick:
    return Tick(
        ts=ts, bid=mid - spread / 2, ask=mid + spread / 2, volume=volume, source="t", received_at=ts
    )


# --------------------------------------------------------------------------- alignment


def test_intraday_buckets_align_to_utc_boundaries() -> None:
    ts = utc("2026-10-13T12:34:56")
    assert bucket("M1", ts, SESSIONS) == (utc("2026-10-13T12:34"), utc("2026-10-13T12:35"))
    assert bucket("M15", ts, SESSIONS) == (utc("2026-10-13T12:30"), utc("2026-10-13T12:45"))
    assert bucket("H1", ts, SESSIONS) == (utc("2026-10-13T12:00"), utc("2026-10-13T13:00"))


def test_daily_and_h4_candles_follow_the_new_york_close_through_dst() -> None:
    # Summer: trading day starts 17:00 EDT = 21:00 UTC.
    assert bucket("D1", utc("2026-10-13T12:00"), SESSIONS) == (
        utc("2026-10-12T21:00"),
        utc("2026-10-13T21:00"),
    )
    assert bucket("H4", utc("2026-10-13T12:00"), SESSIONS) == (
        utc("2026-10-13T09:00"),
        utc("2026-10-13T13:00"),
    )
    # US back on EST from 1 Nov: Monday 2 Nov's trading day starts Sun 17:00 EST = 22:00 UTC.
    assert bucket("D1", utc("2026-11-02T12:00"), SESSIONS)[0] == utc("2026-11-01T22:00")
    # Friday 30 Oct is still EDT: its trading day runs 17:00 to 17:00 New York.
    start, end = bucket("D1", utc("2026-10-30T12:00"), SESSIONS)
    assert start == utc("2026-10-29T21:00") and end == utc("2026-10-30T21:00")


def test_builder_completes_candles_and_tracks_ohlcv() -> None:
    builder = CandleBuilder("t", SESSIONS, timeframes=("M1", "M5"))
    t0 = utc("2026-10-13T12:30:05")
    assert builder.update(tick(t0, 2650.0)) == []
    builder.update(tick(t0 + timedelta(seconds=20), 2652.0))
    builder.update(tick(t0 + timedelta(seconds=40), 2649.0))
    done = builder.update(tick(t0 + timedelta(seconds=61), 2651.0))
    assert [c.tf for c in done] == ["M1"]
    first = done[0]
    assert (first.open, first.high, first.low, first.close, first.ticks) == (
        2650.0,
        2652.0,
        2649.0,
        2649.0,
        3,
    )
    assert first.complete
    # A late tick for the closed minute is ignored.
    assert builder.update(tick(t0 + timedelta(seconds=30), 2700.0)) == []
    assert builder.candles("M1")[-1].high == 2651.0
    assert [c.tf for c in builder.close_due(utc("2026-10-13T12:40"))] == ["M1", "M5"]


def test_aggregate_rolls_h1_into_trading_day_candles() -> None:
    h1 = [
        Candle(
            source="t",
            tf="H1",
            open_time=utc("2026-10-12T21:00") + timedelta(hours=i),
            close_time=utc("2026-10-12T22:00") + timedelta(hours=i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100.5 + i,
            volume=1,
        )
        for i in range(24)
    ]
    (d1,) = aggregate(h1, "D1", SESSIONS)
    assert d1.open == 100 and d1.close == 123.5 and d1.high == 124 and d1.low == 99
    assert d1.complete and d1.volume == 24
    assert len(aggregate(h1, "H4", SESSIONS)) == 6


# --------------------------------------------------------------------------- indicators


def naive_ema(values: list[float], n: int) -> list[float]:
    out = [math.nan] * len(values)
    out[n - 1] = sum(values[:n]) / n
    k = 2 / (n + 1)
    for i in range(n, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def naive_wilder_rsi(values: list[float], n: int) -> float:
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, loss in zip(gains[n:], losses[n:], strict=True):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + loss) / n
    return 100 - 100 / (1 + avg_g / avg_l)


def random_walk(n: int, seed: int = 3) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(2600 + np.cumsum(rng.normal(0, 2, n)))


def test_ema_matches_the_textbook_recursion() -> None:
    values = random_walk(120)
    np.testing.assert_allclose(ind.ema(values, 20), naive_ema(values, 20), equal_nan=True)


def test_rsi_matches_wilder_and_hits_the_extremes() -> None:
    values = random_walk(200)
    assert ind.last(ind.rsi(values, 14)) == pytest.approx(naive_wilder_rsi(values, 14))
    assert ind.last(ind.rsi([float(i) for i in range(30)], 14)) == 100.0
    assert ind.last(ind.rsi([float(-i) for i in range(30)], 14)) == 0.0
    assert ind.last(ind.rsi([5.0] * 30, 14)) == 50.0


def test_atr_bollinger_vwap_pivots_and_swings() -> None:
    highs, lows, closes = [11.0] * 30, [9.0] * 30, [10.0] * 30
    assert ind.last(ind.atr(highs, lows, closes, 14)) == pytest.approx(2.0)
    mid, upper, lower = ind.bollinger([10.0] * 25)
    assert ind.last(mid) == ind.last(upper) == ind.last(lower) == 10.0
    assert ind.vwap([2.0], [0.0], [1.0], [0.0]) == (1.0, True)
    value, twap = ind.vwap([2.0, 4.0], [0.0, 2.0], [1.0, 3.0], [1.0, 3.0])
    assert value == pytest.approx(2.5) and not twap
    pivots = ind.pivots_classic(110, 90, 100)
    assert pivots["P"] == 100 and pivots["R1"] == 110 and pivots["S1"] == 90
    highs_s, lows_s = ind.swing_points([1, 2, 5, 2, 1, 2, 1], [1, 0, 3, 2, -1, 2, 1], 2)
    assert highs_s == [(2, 5)] and lows_s == [(4, -1)]
    assert ind.percentile_rank([1.0, 2.0, 3.0, 4.0], 4.0) == pytest.approx(87.5)


# --------------------------------------------------------------------------- levels + USD


def test_levels_include_previous_day_round_numbers_and_asia_range() -> None:
    d1 = [
        Candle(
            source="t",
            tf="D1",
            open_time=utc("2026-10-11T21:00"),
            close_time=utc("2026-10-12T21:00"),
            open=2600,
            high=2640,
            low=2590,
            close=2630,
            complete=True,
        )
    ]
    m5 = [
        Candle(
            source="t",
            tf="M5",
            open_time=utc("2026-10-12T22:00") + timedelta(minutes=5 * i),
            close_time=utc("2026-10-12T22:05") + timedelta(minutes=5 * i),
            open=2630,
            high=2631 + (i % 7),
            low=2628 - (i % 5),
            close=2630,
            complete=True,
        )
        for i in range(60)
    ]
    now = utc("2026-10-13T12:00")
    asia = asia_range(m5, now, SESSIONS)
    assert asia == (2637, 2624)
    levels = compute_levels(
        price=2633,
        now=now,
        d1=d1,
        h1=[],
        asia=asia,
        sessions=SESSIONS,
        round_steps=(10, 50),
        swing_half_width=3,
    )
    names = {lvl.name for lvl in levels}
    assert {"high kemarin", "low kemarin", "high sesi Asia", "pivot harian P"} <= names
    above = nearest_above(levels, 2633)
    below = nearest_below(levels, 2633)
    assert above is not None and above.price > 2633
    assert below is not None and below.price < 2633
    assert any(lvl.price == 2640 and lvl.kind == "round" for lvl in levels)


def test_synthetic_dxy_uses_the_ice_formula() -> None:
    assert synthetic_dxy(
        dict.fromkeys(("EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF"), 1.0)
    ) == pytest.approx(50.14348112)
    realistic = synthetic_dxy(
        {
            "EURUSD": 1.08,
            "USDJPY": 150.0,
            "GBPUSD": 1.27,
            "USDCAD": 1.36,
            "USDSEK": 10.5,
            "USDCHF": 0.88,
        }
    )
    assert realistic is not None and 100 < realistic < 108
    assert synthetic_dxy({"EURUSD": 1.08}) is None


def test_usd_tracker_flags_an_unusual_move() -> None:
    tracker = UsdTracker(source="test", min_samples=60)
    rng = np.random.default_rng(1)
    start = utc("2026-10-12T00:00")
    value = 1 / 1.08
    for minute in range(600):
        value *= math.exp(rng.normal(0, 0.0001))
        tracker.update(start + timedelta(minutes=minute), value)
    calm = tracker.zscore(start + timedelta(minutes=599), 15)
    assert calm is not None and abs(calm) < 4
    tracker.update(start + timedelta(minutes=600), value * 1.004)  # sharp USD jump
    jump = tracker.zscore(start + timedelta(minutes=600), 15)
    assert jump is not None and jump > 5

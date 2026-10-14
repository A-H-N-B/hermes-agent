"""Event study and learned source reliability on synthetic price paths with known answers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from gold_agent.evaluation.event_study import (
    group,
    measure,
    render_markdown,
    typical_event_moves,
)
from gold_agent.evaluation.reliability import learn_reliability
from gold_agent.market.series import PriceSeries
from gold_agent.models import Candle, ReleaseReading
from tests.builders import T0, analysis


def bars(start: datetime, minutes: int, price: Callable[[int], float]) -> list[Candle]:
    return [
        Candle(
            source="mt5",
            tf="M1",
            open_time=start + timedelta(minutes=m),
            close_time=start + timedelta(minutes=m + 1),
            open=price(m),
            high=price(m) + 0.2,
            low=price(m) - 0.2,
            close=price(m + 1),
        )
        for m in range(minutes)
    ]


def release(key: str) -> ReleaseReading:
    return ReleaseReading(
        event_key=key,
        event_name=key,
        headline=key,
        actual=1.0,
        unit="%",
        first_seen_at=T0,
        parsed_at=T0,
    )


def test_moves_are_measured_from_first_seen_and_signed_by_direction() -> None:
    # gold rises $0.10 per minute for two hours
    prices = PriceSeries.from_candles(
        bars(T0 - timedelta(minutes=10), 130, lambda m: 2600 + m * 0.1)
    )
    stories = [
        analysis("up", first_seen=T0, direction_gold="bullish"),
        analysis("down", first_seen=T0, direction_gold="bearish"),
        analysis("flat", first_seen=T0, direction_gold="neutral"),
        analysis("noise", first_seen=T0, is_gold_relevant=False),
    ]
    moves = {m.cluster_id: m for m in measure(stories, prices)}
    assert set(moves) == {"up", "down", "flat"}
    assert moves["up"].moves[15] == pytest.approx(1.5)
    assert moves["up"].moves[60] == pytest.approx(6.0)
    assert moves["up"].signed(15) == pytest.approx(1.5)
    assert moves["down"].signed(15) == pytest.approx(-1.5)
    assert moves["flat"].signed(15) is None  # no direction, no hit/miss
    late = analysis("late", first_seen=T0 + timedelta(minutes=100))
    assert measure([late], prices)[0].moves[60] is None  # beyond the data: no invented price


def test_grouped_hit_rate_and_typical_release_moves() -> None:
    prices = PriceSeries.from_candles(
        bars(T0 - timedelta(minutes=10), 24 * 60, lambda m: 2600 + (m % 120) * 0.1)
    )
    stories = []
    for i in range(6):  # six CPI releases, gold up after each; five read bullish, one bearish
        at = T0 + timedelta(minutes=120 * i)
        direction = "bearish" if i == 0 else "bullish"
        item = analysis(f"cpi{i}", first_seen=at, direction_gold=direction, category="us_inflation")
        stories.append(item.model_copy(update={"release": release("us_cpi_mom")}))
    moves = measure(stories, prices)
    (cpi,) = group(moves, lambda m: m.category, 15)
    assert (cpi.n, cpi.hits) == (6, 5) and cpi.hit_rate == pytest.approx(5 / 6)
    assert cpi.wilson[0] < 5 / 6 < cpi.wilson[1]
    assert cpi.median_abs == pytest.approx(1.5) and not cpi.enough
    assert typical_event_moves(moves, min_samples=5) == {"us_cpi_mom": pytest.approx(6.0)}
    assert typical_event_moves(moves, min_samples=7) == {}
    report = render_markdown(moves, start=T0, end=T0 + timedelta(days=1))
    assert "**Sampel belum cukup** (6 < 30)" in report
    assert "| us_inflation (sampel belum cukup) | 6 | 83% |" in report


def test_reliability_learning_shrinks_towards_the_prior() -> None:
    prices = PriceSeries.from_candles(
        bars(T0 - timedelta(minutes=10), 24 * 60, lambda m: 2600 + (m % 120) * 0.1)
    )
    good = [
        analysis(f"g{i}", first_seen=T0 + timedelta(minutes=120 * i), sources=("wire",))
        for i in range(10)
    ]
    bad = [
        analysis(
            f"b{i}",
            first_seen=T0 + timedelta(minutes=120 * i),
            sources=("rumours",),
            direction_gold="bearish",
        )
        for i in range(10)
    ]
    few = [analysis("x", first_seen=T0, sources=("new",))]
    moves = measure(good + bad + few, prices)
    learned = learn_reliability(
        moves,
        {"wire": 0.8, "rumours": 0.6, "new": 0.7},
        prior_weight=20,
        min_samples=10,
    )
    assert learned["wire"] == (pytest.approx((0.8 * 20 + 10) / 30, abs=1e-4), 10)
    assert learned["rumours"] == (pytest.approx((0.6 * 20 + 0) / 30, abs=1e-4), 10)
    assert "new" not in learned  # one story proves nothing

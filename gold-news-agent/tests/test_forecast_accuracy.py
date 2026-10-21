"""Forecast accuracy, walk-forward: each bar's forecast is rebuilt from what was known at its
close and scored on the next close. Abstentions (NETRAL, TUNGGU DATA) and bars without a stored
engine score never count as calls, and the news and chart parts are scored on the same bars."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import pytest

from gold_agent.dashboard.forecast_score import Held, forecast_accuracy, wilson
from gold_agent.models import Candle
from gold_agent.timeutils.windows import WindowSpan
from tests.builders import T0, event

STEP = timedelta(minutes=5)
BARS = 260
WARMUP = 30  # bars before the measured period, as the dashboard loads them
CALLS = BARS - 1 - WARMUP  # every measured bar that has a closed next bar


def bars_from(closes: Sequence[float]) -> list[Candle]:
    out: list[Candle] = []
    for i, close in enumerate(closes):
        opened = T0 + i * STEP
        before = closes[i - 1] if i else close
        out.append(
            Candle(
                source="mt5",
                tf="M5",
                open_time=opened,
                close_time=opened + STEP,
                open=before,
                high=max(before, close) + 0.3,
                low=min(before, close) - 0.3,
                close=close,
            )
        )
    return out


def every_minute(start: datetime, end: datetime, score: float) -> list[tuple[datetime, float]]:
    minutes = int((end - start).total_seconds() // 60)
    return [(start + timedelta(minutes=m), score) for m in range(minutes + 1)]


def rising() -> list[Candle]:
    return bars_from([2600.0 + i for i in range(BARS)])


def falling() -> list[Candle]:
    return bars_from([2900.0 - i for i in range(BARS)])


def measure(
    bars: Sequence[Candle],
    score: float | None = None,
    *,
    scores: Sequence[tuple[datetime, float]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if scores is None:
        assert score is not None
        scores = every_minute(T0, bars[-1].close_time, score)
    options: dict[str, Any] = {"held": [], "events": [], "spans": None} | extra
    return forecast_accuracy(
        tf="M5",
        bars=bars,
        scores=scores,
        threshold=0.35,
        confirm=timedelta(seconds=60),
        now=bars[-1].close_time + timedelta(minutes=1),
        start=bars[WARMUP].close_time,
        **options,
    )


def test_wilson_interval() -> None:
    low, high = wilson(50, 100) or (0, 0)
    assert (round(low, 4), round(high, 4)) == (0.4038, 0.5962)
    low, high = wilson(10, 10) or (0, 0)
    assert (round(low, 4), high) == (0.7225, pytest.approx(1.0))
    assert wilson(0, 0) is None


@pytest.mark.parametrize(("bars", "engine"), [(rising(), 0.35), (falling(), -0.35)])
def test_a_forecast_that_agrees_with_the_market_is_scored_right_both_ways(
    bars: list[Candle], engine: float
) -> None:
    result = measure(bars, engine)
    assert result["forecast"] == {
        "calls": CALLS,
        "hits": CALLS,
        "rate": 1.0,
        "ci95": result["forecast"]["ci95"],
    }
    assert result["forecast"]["ci95"][0] > 0.98
    assert result["news_only"]["rate"] == 1.0


def test_a_wrong_engine_is_reported_as_wrong_and_the_chart_part_on_its_own() -> None:
    result = measure(rising(), -0.35)  # bearish engine in a rising market
    assert result["forecast"]["rate"] == 0.0 and result["news_only"]["rate"] == 0.0
    assert result["chart_only"]["rate"] == 1.0  # the indicators read the rise correctly


def test_no_engine_score_means_skipped_and_a_release_means_tunggu_data() -> None:
    bars = rising()
    first_score = bars[99].close_time
    release = event("CPI m/m", at=bars[150].close_time + timedelta(minutes=2))
    result = measure(
        bars,
        scores=every_minute(first_score, bars[-1].close_time, 0.35),
        events=[release],
    )
    assert result["skipped_no_engine"] == 99 - WARMUP  # until bar 98 the agent was not running
    assert result["abstained"] == 1  # bar 150: the release falls inside its next candle
    assert result["forecast"]["calls"] == CALLS - (99 - WARMUP) - 1


def test_only_bars_inside_the_trading_windows_count() -> None:
    bars = rising()
    span = WindowSpan("London", "M5", bars[200].open_time, bars[210].open_time)
    result = measure(bars, 0.35, spans=[span])
    assert result["scope"] == "window" and result["forecast"]["calls"] == 10


def test_an_active_signal_counts_at_full_strength() -> None:
    bars = rising()
    held: list[Held] = [(bars[50].close_time, bars[60].close_time, 1.0)]
    result = measure(bars, -0.35, held=held)  # bearish score, but a BUY was live for 10 bars
    assert result["news_only"]["hits"] == 10

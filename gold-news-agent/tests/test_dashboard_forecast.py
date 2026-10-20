"""Timeframe ring forecast: what the agent expects for the next candle of the chosen timeframe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from gold_agent.config import Settings
from gold_agent.dashboard.forecast import aggregate, forecast_next
from gold_agent.models import Candle, SignalComponents, SignalState
from tests.builders import T0, event, market
from tests.test_notify_templates import make_signal
from tests.test_replay_bridge import history


def _state(score: float) -> SignalState:
    parts = SignalComponents(N=score, M=0.0, T=0.0, P=0.0, session_factor=1.0)
    return SignalState(ts=T0, action="WAIT", composite_score=score, confidence=40, components=parts)


def _run(settings: Settings, **overrides: object) -> dict[str, Any]:
    args: dict[str, object] = {
        "tf": "M5",
        "bars": history("M5", T0, 260),  # steady uptrend
        "now": T0,
        "settings": settings,
        "state": _state(0.0),
        "active": None,
        "events": [],
        "context": market(),
    }
    return forecast_next(**(args | overrides))  # type: ignore[arg-type]


def test_rising_chart_and_bullish_news_forecast_up_for_the_next_candle(
    settings: Settings,
) -> None:
    out = _run(settings, state=_state(settings.signal_engine.entry_threshold))
    assert out["direction"] == "NAIK" and out["horizon"] == "5 menit"
    expected = out["expected"]
    assert isinstance(expected, dict) and expected["target"] > out["close"]
    assert expected["low"] < expected["target"] < expected["high"]


def test_strong_bearish_news_outweighs_a_rising_chart(settings: Settings) -> None:
    out = _run(settings, state=_state(-settings.signal_engine.entry_threshold))
    assert out["score"] < 0  # 0.6 x news (-1) outweighs 0.4 x a fully rising chart (+1)
    assert out["direction"] in ("TURUN", "NETRAL")  # but only just: no strong call


def test_an_active_signal_counts_at_full_strength(settings: Settings) -> None:
    signal = make_signal(settings)  # BUY
    out = _run(settings, active=signal, state=_state(-0.1))
    assert out["direction"] == "NAIK" and out["strength"] >= 90


def test_a_release_inside_the_horizon_means_wait_for_data(settings: Settings) -> None:
    cpi = event("CPI m/m", at=T0 + timedelta(minutes=30))
    assert _run(settings, tf="H1", bars=history("H1", T0, 260), events=[cpi])["direction"] == (
        "TUNGGU DATA"
    )
    assert _run(settings, events=[cpi])["direction"] != "TUNGGU DATA"  # beyond the next M5 bar


def test_closed_market_and_missing_data_say_so(settings: Settings) -> None:
    assert _run(settings, context=market(market_open=False))["direction"] == "PASAR TUTUP"
    assert _run(settings, bars=[])["direction"] == "DATA BELUM ADA"


def test_daily_bars_roll_up_into_weeks_and_months() -> None:
    def day(d: int, month: int = 9) -> Candle:
        close = datetime(2026, month, d, 21, 0, tzinfo=UTC)  # 17:00 New York
        return Candle(
            source="mt5",
            tf="D1",
            open_time=close - timedelta(days=1),
            close_time=close,
            open=100 + d,
            high=110 + d,
            low=90 + d,
            close=105 + d,
        )

    daily = [day(d) for d in (28, 29, 30)] + [day(1, 10), day(2, 10), day(5, 10)]
    weeks = aggregate(daily, "W1")  # Mon 28 Sep - Fri 2 Oct, then Mon 5 Oct
    assert [(w.open, w.close) for w in weeks] == [(128, 107), (105, 110)]
    assert weeks[0].high == 140 and weeks[0].low == 91
    months = aggregate(daily, "MN")
    assert [(m.open, m.close) for m in months] == [(128, 135), (101, 110)]


def _mirror(bars: list[Candle], pivot: float = 2600.0) -> list[Candle]:
    """The same market upside down: every price reflected around `pivot`."""
    return [
        b.model_copy(
            update={
                "open": 2 * pivot - b.open,
                "close": 2 * pivot - b.close,
                "high": 2 * pivot - b.low,
                "low": 2 * pivot - b.high,
            }
        )
        for b in bars
    ]


def test_buy_and_sell_are_symmetric_a_falling_market_with_bearish_news_reads_down(
    settings: Settings,
) -> None:
    threshold = settings.signal_engine.entry_threshold
    up_bars = history("M5", T0, 260)
    up = _run(settings, bars=up_bars, state=_state(threshold * 0.5))
    down = _run(settings, bars=_mirror(up_bars), state=_state(-threshold * 0.5))
    assert (up["direction"], down["direction"]) == ("NAIK", "TURUN")
    assert down["score"] == -up["score"] and down["strength"] == up["strength"]

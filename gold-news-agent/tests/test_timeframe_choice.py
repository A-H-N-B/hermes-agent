"""Automatic timeframe: usable charts only (data, spread next to the stop), the most accurate one
proven in this session, else the session default (M5 morning to afternoon, M15 from the New York
open); every signal is sized, timed and valid on it."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.models import Level, MarketContext, TimeframeState
from gold_agent.signals.engine import Decision, EngineState, SignalEngine
from gold_agent.signals.timeframe import MIN_CALLS, TfChoice, choose_timeframe
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, flat_prices, market

NOW = T0 + timedelta(minutes=5)  # Tuesday 19:35 WIB, London–New York overlap
LEVELS = [
    Level(name="swing low H1", price=2644.0, kind="support"),
    Level(name="high kemarin", price=2668.0, kind="resistance"),
]


def tally(rate: float, calls: int) -> dict[str, Any]:
    """A walk-forward tally with a 95% interval of about ±4 points."""
    return {
        "calls": calls,
        "hits": round(rate * calls),
        "rate": rate,
        "ci95": [rate - 0.04, rate + 0.04],
    }


def charted(ts: datetime = NOW, **frames: TimeframeState) -> MarketContext:
    base = market(ts=ts, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=LEVELS)
    return base.model_copy(update={"timeframes": base.timeframes | frames})


def frame(
    tf: Any, *, ema20: float | None = 2649.0, atr: float = 4.0, **extra: Any
) -> TimeframeState:
    return TimeframeState(tf=tf, ema20=ema20, atr14=atr, trend="up", bars=200, **extra)


def choose(settings: Settings, session: str, **kwargs: Any) -> TfChoice:
    defaults: dict[str, Any] = {"context": charted(), "accuracy": {}}
    return choose_timeframe(session=session, settings=settings, **(defaults | kwargs))


def decide(settings: Settings, context: MarketContext, choice: TfChoice | None = None) -> Decision:
    engine = SignalEngine(settings, SessionClock(settings.sessions), config_version="cfg")
    return engine.evaluate(
        now=NOW,
        context=context,
        analyses=[analysis(first_seen=T0)],
        events=[],
        state=EngineState(),
        price_at=flat_prices(2645.0),
        timeframe=choice,
    )


# ------------------------------------------------------------------ which chart


@pytest.mark.parametrize(
    ("session", "expected"),
    [("asia", "M5"), ("london", "M5"), ("overlap", "M15"), ("new_york", "M15"), ("closed", "M5")],
)
def test_without_evidence_the_session_default_applies(
    settings: Settings, session: str, expected: str
) -> None:
    choice = choose(settings, session)
    assert (choice.tf, choice.basis) == (expected, "prior")


def test_the_most_accurate_proven_timeframe_wins(settings: Settings) -> None:
    accuracy = {
        "M5": {"overlap": tally(0.53, 900)},  # lower bound 0.49: no edge proven
        "M15": {"overlap": tally(0.58, 300)},  # lower bound 0.54
        "M30": {"overlap": tally(0.62, 30)},  # better rate, too few calls to count
    }
    choice = choose(settings, "overlap", accuracy=accuracy)
    assert (choice.tf, choice.basis) == ("M15", "data")
    assert choice.reason == "M15: akurasi sesi London–New York 58% (95%: 54%–62%, 300 candle)"
    m30 = next(o for o in choice.options if o.tf == "M30")
    assert m30.calls < MIN_CALLS


def test_a_timeframe_whose_spread_eats_the_stop_is_skipped(settings: Settings) -> None:
    wide = charted(M5=frame("M5", atr=0.4))  # 0.20 spread on a 0.48 stop: 42%
    choice = choose(settings, "london", context=wide)
    assert (choice.tf, choice.basis) == ("M15", "fallback")
    m5 = next(o for o in choice.options if o.tf == "M5")
    assert not m5.eligible and m5.note == "spread 42% dari SL"
    assert choice.reason == "M5 belum layak (spread 42% dari SL), pakai M15"


def test_the_current_timeframe_is_kept_unless_clearly_beaten(settings: Settings) -> None:
    accuracy = {"M5": {"london": tally(0.57, 600)}, "M15": {"london": tally(0.58, 600)}}
    kept = choose(settings, "london", accuracy=accuracy, current="M5")
    assert kept.tf == "M5"  # one point better is noise
    accuracy["M15"] = {"london": tally(0.61, 600)}
    assert choose(settings, "london", accuracy=accuracy, current="M5").tf == "M15"


def test_a_fixed_timeframe_from_config_is_used_as_is(settings: Settings) -> None:
    fixed = settings.trading.timeframe.model_copy(update={"select": "H1"})
    pinned = settings.model_copy(
        update={"trading": settings.trading.model_copy(update={"timeframe": fixed})}
    )
    choice = choose(pinned, "london")
    assert (choice.tf, choice.basis) == ("H1", "fixed")


# ------------------------------------------------------------------ the engine on that chart


def test_every_signal_is_sized_timed_and_valid_on_the_chosen_chart(settings: Settings) -> None:
    m15 = frame("M15", ema20=2648.0, swing_low=2645.0)
    decision = decide(settings, charted(M15=m15))
    signal = decision.signal
    assert signal is not None and signal.timeframe == "M15"
    assert signal.timeframe_reason is not None and signal.timeframe_reason.startswith("bawaan")
    # Stop beyond the nearest M15 swing (2645.00 - 0.1 ATR): 5.50 below 2650.10.
    assert (signal.stop_loss, signal.sl_distance) == (2644.6, 5.5)
    assert signal.valid_until_utc == NOW + timedelta(minutes=90)  # 6 candles of M15
    assert "candle M15 ditutup kembali di bawah EMA20 M15 (2648.00)" in signal.invalidation
    assert decision.state.timeframe == "M15"


@pytest.mark.parametrize(
    ("ema20", "reason"),
    [
        (2652.0, "timing M15: tunggu harga kembali di atas EMA20 M15 (2652.00)"),
        (2640.0, "timing M15: harga sudah 2.5 ATR dari EMA20 M15 (2640.00); tunggu pullback"),
    ],
)
def test_the_entry_is_timed_on_the_chosen_chart(
    settings: Settings, ema20: float, reason: str
) -> None:
    decision = decide(settings, charted(M15=frame("M15", ema20=ema20)))
    assert decision.signal is None
    assert any(text.startswith(reason) for text in decision.state.wait_reasons)

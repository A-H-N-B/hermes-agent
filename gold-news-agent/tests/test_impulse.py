"""Momentum impulses (market/impulse.py, signals/impulses.py, the engine's impulse path): the start
of a big move is found the second it happens, becomes a candidate before the slower score
catches up, is entered near where it began or not at all, and is judged by an honest
walk-forward record of past impulses."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from gold_agent.config import Settings
from gold_agent.market.candles import aggregate
from gold_agent.market.impulse import detect, triggers
from gold_agent.models import Candle, Impulse, Level, MarketContext, TimeframeState
from gold_agent.signals.engine import Decision, EngineState, SignalEngine
from gold_agent.signals.impulses import impulse_record, replay_impulses
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, flat_prices, market

NOW = T0 + timedelta(minutes=5)
START = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
LEVELS = [Level(name="high kemarin", price=2668.0, kind="resistance")]


def minutes(closes: list[float], start: datetime = START, wick: float = 0.2) -> list[Candle]:
    bars: list[Candle] = []
    for i, close in enumerate(closes):
        opened = closes[i - 1] if i else close
        at = start + timedelta(minutes=i)
        bars.append(
            Candle(
                source="x",
                tf="M1",
                open_time=at,
                close_time=at + timedelta(minutes=1),
                open=opened,
                high=max(opened, close) + wick,
                low=min(opened, close) - wick,
                close=close,
            )
        )
    return bars


def walk(seed: int, n: int, *, bursts: bool = False) -> list[float]:
    """Fat-tailed minute moves (sigma about 0.9 $); with `bursts`, moves that keep going for
    25 minutes now and then (real momentum)."""
    rng = np.random.default_rng(seed)
    steps = rng.standard_t(4, n) * 0.9 / math.sqrt(2)
    if bursts:
        k = 0
        while k < n - 40:
            if rng.random() < 0.004:
                steps[k : k + 25] += rng.choice([-1.0, 1.0]) * 0.9
                k += 40
            k += 1
    return list(2650.0 + np.cumsum(steps))


# ------------------------------------------------------------------ live detection


def test_a_burst_out_of_the_range_fires_at_its_trigger_and_not_before() -> None:
    bars = minutes([2650.0 + 0.3 * math.sin(i) for i in range(150)])  # a quiet range
    trig = triggers(bars, z=3.0, range_minutes=30)
    assert trig is not None and trig.up >= trig.high and trig.down <= trig.low
    since = bars[-1].close_time
    quiet = detect(trig, trig.up - 0.01, atr=2.0, aligned_trend="up", since=since)
    assert quiet is None
    burst = detect(trig, trig.up + 0.5, atr=2.0, aligned_trend="up", since=since)
    assert burst is not None and burst.side == "BUY" and burst.z >= 3.0
    assert burst.trigger == round(trig.up, 2) and burst.extension_atr == 0.25
    assert burst.aligned is True
    down = detect(trig, trig.down - 0.1, atr=2.0, aligned_trend="up", since=since)
    assert down is not None and down.side == "SELL" and down.aligned is False


def test_without_recent_contiguous_minutes_there_is_no_trigger() -> None:
    bars = minutes([2650.0] * 40)  # too short a history for the per-minute volatility
    assert triggers(bars, z=3.0, range_minutes=30) is None
    gappy = minutes([2650.0 + 0.3 * math.sin(i) for i in range(150)])[::2]  # every other minute
    assert triggers(gappy, z=3.0, range_minutes=30) is None


# ------------------------------------------------------------------ the walk-forward record


@pytest.mark.parametrize(("bursts", "edge"), [(False, False), (True, True)])
def test_the_record_shows_an_edge_only_where_there_is_one(
    settings: Settings, bursts: bool, edge: bool
) -> None:
    m1 = minutes(walk(7, 5 * 1440, bursts=bursts))
    m5 = aggregate(m1, "M5", SessionClock(settings.sessions))
    setups = replay_impulses(
        m1=m1,
        sizing=m5,
        z=3.0,
        range_minutes=30,
        max_extension_atr=1.5,
        sl_atr=1.2,
        tp1_r=1.0,
        validity_minutes=30,
        now=m1[-1].close_time,
        start=START + timedelta(hours=3),
    )
    record = impulse_record(setups, lambda _at: "london")
    low, high = record["ci95"]
    assert record["n"] > 50
    if edge:  # momentum that keeps going: impulses reach TP1 first well beyond a coin flip
        assert low > 0.55
    else:  # a random walk: no edge to find, and the record does not invent one
        assert low < 0.5 < high


# ------------------------------------------------------------------ the engine's impulse path


def impulse(side: str = "BUY", **extra: Any) -> Impulse:
    base: dict[str, Any] = {
        "side": side,
        "z": 4.0,
        "window": 3,
        "move": 6.0 if side == "BUY" else -6.0,
        "level": 2649.5 if side == "BUY" else 2650.5,
        "trigger": 2649.8 if side == "BUY" else 2650.2,
        "range_minutes": 30,
        "extension_atr": 0.15,
        "aligned": True,
        "since": NOW - timedelta(seconds=3),
    }
    return Impulse.model_validate(base | extra)


def quiet_market(**extra: Any) -> MarketContext:
    """No news, no macro, flat trends: the slower score is about zero."""
    base = market(trend_h1="flat", trend_h4="flat", rsi_h1=50.0, ts=NOW, levels=LEVELS)
    return base.model_copy(update=extra)


def decide(
    settings: Settings,
    context: MarketContext,
    *,
    news: bool = False,
    record: dict[str, Any] | None = None,
) -> Decision:
    engine = SignalEngine(settings, SessionClock(settings.sessions), config_version="cfg")
    return engine.evaluate(
        now=NOW,
        context=context,
        analyses=[analysis(first_seen=T0)] if news else [],
        events=[],
        state=EngineState(),
        price_at=flat_prices(2650.0 if not news else 2645.0),
        impulse_record=record,
    )


def test_an_impulse_is_a_candidate_before_the_slower_score_catches_up(
    settings: Settings,
) -> None:
    without = decide(settings, quiet_market())
    assert without.signal is None
    assert without.state.wait_reasons[0].startswith("skor gabungan +0.00 di bawah ambang")
    record = {"tp1": 30, "sl": 15, "expired": 5, "n": 50, "rate": 0.67, "ci95": [0.52, 0.79]}
    decision = decide(settings, quiet_market(impulse=impulse()), record=record)
    signal = decision.signal
    assert signal is not None and signal.action == "BUY" and signal.setup_type == "impulse"
    assert signal.timeframe == "M5"  # sized on the chart its record was replayed on
    assert signal.top_reasons[0].startswith("Impuls harga naik kuat: +6.00$ dalam 3 mnt")
    assert signal.impulse_note is not None and "30 kena TP1, 15 kena SL" in signal.impulse_note
    assert signal.odds is not None and signal.odds.setups == 50  # blended with past impulses
    assert signal.confidence >= settings.signal_engine.min_confidence


@pytest.mark.parametrize(
    ("context", "reason"),
    [
        (  # the slower score leans the other way
            market(trend_h1="down", trend_h4="down", rsi_h1=40.0, ts=NOW, levels=LEVELS),
            "impuls naik tetapi skor gabungan -0.16 berlawanan",
        ),
        (  # already well past where it began
            quiet_market(),
            "impuls sudah lewat 2.0 ATR M5 dari awalnya (2649.80)",
        ),
    ],
)
def test_an_impulse_waits_when_opposed_or_already_gone(
    settings: Settings, context: MarketContext, reason: str
) -> None:
    far = 2.0 if "lewat" in reason else 0.15
    decision = decide(settings, context.model_copy(update={"impulse": impulse(extension_atr=far)}))
    assert decision.signal is None
    assert any(text.startswith(reason) for text in decision.state.wait_reasons)


def test_a_same_side_impulse_replaces_the_pullback_rule(settings: Settings) -> None:
    stretched = market(ts=NOW, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=LEVELS)
    m15 = TimeframeState(tf="M15", ema20=2640.0, atr14=4.0, trend="up", bars=200)
    stretched = stretched.model_copy(update={"timeframes": stretched.timeframes | {"M15": m15}})
    chased = decide(settings, stretched, news=True)
    assert chased.signal is None  # 2.5 ATR past EMA20 M15: normally waits for a pullback
    assert any("tunggu pullback" in text for text in chased.state.wait_reasons)
    moving = decide(settings, stretched.model_copy(update={"impulse": impulse()}), news=True)
    assert moving.signal is not None and moving.signal.action == "BUY"
    assert moving.signal.setup_type != "impulse"  # the news score made it; the impulse timed it
    assert moving.signal.impulse_note is not None


def test_a_strong_move_against_the_score_holds_it(settings: Settings) -> None:
    context = market(ts=NOW, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=LEVELS)
    decision = decide(settings, context.model_copy(update={"impulse": impulse("SELL")}), news=True)
    assert decision.signal is None
    assert decision.state.wait_reasons[0].startswith("harga bergerak kuat berlawanan: impuls")


# ------------------------------------------------------------------ in the live context


def test_the_live_context_carries_the_impulse_from_its_first_second(settings: Settings) -> None:
    from collections import deque

    from gold_agent.market.candles import CandleBuilder
    from gold_agent.market.context import ContextCache, build_market_context
    from gold_agent.models import Tick

    sessions = SessionClock(settings.sessions)
    builder = CandleBuilder("mt5", sessions)
    start = NOW - timedelta(minutes=150)
    builder.seed("M1", minutes([2650.0 + 0.3 * math.sin(i) for i in range(150)], start=start))
    for tf in ("M5", "M15"):
        builder.seed(tf, aggregate(builder.candles("M1"), tf, sessions))
    cache = ContextCache()

    def at(second: int, price: float) -> MarketContext:
        moment = NOW + timedelta(seconds=second)
        tick = Tick(ts=moment, bid=price - 0.1, ask=price + 0.1, source="mt5", received_at=moment)
        builder.update(tick)  # what the market service does with every tick
        return build_market_context(
            now=moment,
            tick=tick,
            builder=builder,
            sessions=sessions,
            settings=settings,
            spreads=deque([(moment, 0.2)]),
            cache=cache,
        )

    assert at(0, 2650.1).impulse is None
    first = at(1, 2656.0).impulse
    assert first is not None and first.side == "BUY"
    later = at(2, 2656.5)
    assert later.impulse is not None and later.impulse.since == first.since  # one impulse
    assert later.timeframes["M15"].close == 2656.5  # slow charts reused, their close current

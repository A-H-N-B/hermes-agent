"""Peluang TP1 sebelum SL and the expected value (signals/odds.py, signals/setups.py): honest
odds without a proven edge, better odds with one, the record taking over as it grows, and a
plan whose kind has lost money beyond doubt waiting instead of being sent."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.models import Candle, Level, TimeframeState
from gold_agent.signals.engine import EngineState, SignalEngine
from gold_agent.signals.odds import trade_odds
from gold_agent.signals.setups import setup_outcomes
from gold_agent.signals.timeframe import choose_timeframe
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, flat_prices, market

NOW = T0 + timedelta(minutes=5)
STATE = TimeframeState(tf="M15", close=2650.0, atr14=5.0, sigma=3.0, sigma_model="garch")
PLAN: dict[str, Any] = {
    "side": "BUY",
    "entry": 2650.0,
    "stop_loss": 2644.0,
    "tp1": 2656.0,
    "tp2": 2662.0,
    "spread": 0.3,
    "state": STATE,
    "tf": "M15",
    "validity_bars": 6,
    "min_calls": 40,
    "tp1_fraction": 0.5,
}


def proven(lower: float, calls: int = 400, **extra: Any) -> dict[str, Any]:
    return {
        "calls": calls,
        "hits": int(calls * lower),
        "rate": lower + 0.03,
        "ci95": [lower, lower + 0.06],
    } | extra


def test_without_a_proven_edge_a_one_r_target_is_a_coin_flip_minus_the_spread() -> None:
    odds = trade_odds(**PLAN, accuracy=None)
    assert odds is not None and odds.setups == 0
    assert odds.tp1 == pytest.approx(odds.stop, abs=0.03)  # no drift: symmetric
    assert odds.tp2_after_tp1 == pytest.approx(0.5)
    assert odds.ev_r == pytest.approx(-odds.cost_r, abs=0.06) and odds.cost_r == 0.05
    assert "arah dianggap acak" in odds.drift_basis
    assert odds.note.startswith(f"Peluang TP1 sebelum SL {odds.tp1:.0%}")
    unproven = trade_odds(**PLAN, accuracy=proven(0.49))  # lower bound under a coin flip
    assert unproven is not None and unproven.model_tp1 == odds.model_tp1


def test_a_proven_edge_tilts_the_odds_and_the_expected_value() -> None:
    odds = trade_odds(**PLAN, accuracy=proven(0.56))
    flat = trade_odds(**PLAN, accuracy=None)
    assert odds is not None and flat is not None
    assert odds.tp1 > flat.tp1 + 0.05 and odds.stop < flat.stop
    assert odds.ev_r > 0 and odds.tp2_after_tp1 > 0.5
    assert "terbukti" in odds.drift_basis


def plan(**changes: Any) -> dict[str, Any]:
    return {**PLAN, **changes}


def test_the_chart_s_physics_shapes_the_odds() -> None:
    flat = trade_odds(**PLAN, accuracy=None)
    reverting = STATE.model_copy(update={"ou_half_life": 3.0, "ou_mean": 2662.0})
    up = trade_odds(**plan(state=reverting), accuracy=None)
    sell = plan(side="SELL", stop_loss=2656.0, tp1=2644.0, tp2=2638.0, state=reverting)
    against = trade_odds(**sell, accuracy=None)
    assert flat is not None and up is not None and against is not None
    assert up.tp1 > flat.tp1 + 0.05  # BUY towards the level price keeps returning to
    assert against.tp1 < flat.tp1 - 0.05  # SELL away from it
    assert up.revert_half_life == 3.0 and "mean reversion (OU) ke 2662.00" in up.model_note
    fat = trade_odds(**plan(state=STATE.model_copy(update={"tail_nu": 3.0})), accuracy=None)
    assert fat is not None and fat.tail_nu == 3.0 and "Student-t ν=3" in fat.model_note
    assert fat.tp1 == pytest.approx(fat.stop, abs=0.03)  # fat tails alone pick no side
    assert flat.paths == 20_000 and "ekor normal" in flat.model_note


def test_the_record_of_past_setups_takes_over_as_it_grows() -> None:
    losing = {"tp1": 30, "sl": 60, "expired": 10}
    few = trade_odds(**PLAN, accuracy=proven(0.56, setups={"tp1": 3, "sl": 6, "expired": 1}))
    many = trade_odds(**PLAN, accuracy=proven(0.56, setups=losing))
    assert few is not None and many is not None
    assert many.setups == 100 and many.tp1 < few.tp1 < few.model_tp1
    assert many.tp1 == pytest.approx((20 * many.model_tp1 + 30) / 120, abs=1e-3)
    assert many.ev_r_ci95 is not None and many.ev_r_ci95[1] < 0  # losing beyond doubt
    assert "100 setup historis" in many.note


def test_a_plan_whose_kind_lost_money_on_record_waits(settings: Settings) -> None:
    engine = SignalEngine(settings, SessionClock(settings.sessions), config_version="cfg")
    levels = [
        Level(name="swing low H1", price=2644.0, kind="support"),
        Level(name="high kemarin", price=2668.0, kind="resistance"),
    ]
    context = market(ts=NOW, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=levels)

    def decide(setups: dict[str, int]) -> Any:
        record = {"M15": {"overlap": proven(0.52, calls=60, setups=setups)}}
        choice = choose_timeframe(
            context=context, session="overlap", settings=settings, accuracy=record
        )
        return engine.evaluate(
            now=NOW,
            context=context,
            analyses=[analysis(first_seen=T0)],
            events=[],
            state=EngineState(),
            price_at=flat_prices(2645.0),
            timeframe=choice,
        )

    held = decide({"tp1": 25, "sl": 70, "expired": 5})
    assert held.signal is None
    assert held.state.wait_reasons[0].startswith("setup serupa di M15 historis rugi: EV")
    sent = decide({"tp1": 60, "sl": 35, "expired": 5})
    assert sent.signal is not None and sent.signal.odds is not None
    assert sent.signal.odds.setups == 100 and sent.signal.odds.ev_r > 0


def bars(closes: list[tuple[float, float, float]]) -> list[Candle]:
    return [
        Candle(
            source="mt5",
            tf="M15",
            open_time=T0 + timedelta(minutes=15 * i),
            close_time=T0 + timedelta(minutes=15 * (i + 1)),
            open=close,
            high=high,
            low=low,
            close=close,
            complete=True,
        )
        for i, (low, high, close) in enumerate(closes)
    ]


def test_past_setups_are_replayed_one_at_a_time_with_the_cautious_reading() -> None:
    quiet = [(2649.0, 2651.0, 2650.0)] * 20  # ATR(14) = 2: stop 2.4 away, TP1 2.4 away
    path = bars(
        [
            *quiet,
            (2649.5, 2653.0, 2652.0),  # bar 20: TP1 of the setup at bar 19 (2652.4)? not yet
            (2651.0, 2653.5, 2653.0),  # bar 21: TP1 reached
            (2649.0, 2656.0, 2650.0),  # bar 22: a new setup's TP1 and stop in one bar: stop
            *quiet,
        ]
    )
    scores = [(bar.close_time, 0.5) for bar in path]  # the engine said BUY all along
    result = setup_outcomes(
        bars=path,
        scores=scores,
        held=[],
        threshold=0.35,
        sl_atr=1.2,
        tp1_r=1.0,
        validity_bars=6,
        now=path[-1].close_time + timedelta(minutes=1),
        start=path[19].close_time,
        session_of=lambda _: "overlap",
    )
    assert result["tp1"] >= 1 and result["sl"] >= 1
    assert result["n"] == result["tp1"] + result["sl"] + result["expired"]
    assert result["by_session"]["overlap"]["n"] == result["n"]
    # One trade at a time: far fewer setups than bars with a BUY score.
    assert result["n"] < len(path) - 19

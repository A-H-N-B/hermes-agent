"""Paper trade management: realistic fills, partial at TP1, breakeven, gaps, expiry, max hold."""

from __future__ import annotations

from datetime import timedelta

import pytest

from gold_agent.config import Settings
from gold_agent.evaluation.paper import Quote, apply_quote, close_at_market, open_position
from gold_agent.models import Level, Signal, SignalOutcome
from gold_agent.signals.engine import EngineState, SignalEngine
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, flat_prices, market

NOW = T0 + timedelta(minutes=5)
LEVELS = [Level(name="swing low H1", price=2644.0, kind="support")]


def buy(settings: Settings) -> Signal:
    engine = SignalEngine(settings, SessionClock(settings.sessions), config_version="x")
    decision = engine.evaluate(
        now=NOW,
        context=market(ts=NOW, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=LEVELS),
        analyses=[analysis(first_seen=T0)],
        events=[],
        state=EngineState(),
        price_at=flat_prices(2645.0),
    )
    assert decision.signal is not None
    # entry ask 2650.10, SL 2643.60, TP1 2656.60, TP2 2663.10, valid 120 min
    return decision.signal


def quote(minutes: float, bid: float, spread: float = 0.2) -> Quote:
    return Quote(NOW + timedelta(minutes=minutes), bid, bid + spread)


def run(signal: Signal, settings: Settings, *quotes: Quote) -> SignalOutcome:
    outcome = open_position(signal, quotes[0], settings)
    for q in quotes:
        outcome = apply_quote(outcome, signal, q, settings)
    return outcome


def test_fill_pays_spread_and_slippage(settings: Settings) -> None:
    signal = buy(settings)
    opened = open_position(signal, quote(0, 2649.9), settings)
    assert opened.entry_price == pytest.approx(2650.15)  # ask 2650.10 + 0.05 slippage
    assert opened.risk_per_unit == pytest.approx(6.55)  # measured from the fill, not the zone
    assert (opened.status, opened.remaining, opened.stop) == ("open", 1.0, signal.stop_loss)


def test_half_at_tp1_then_the_runner_reaches_tp2(settings: Settings) -> None:
    signal = buy(settings)
    after_tp1 = run(signal, settings, quote(0, 2649.9), quote(10, 2656.6))
    assert after_tp1.tp1_hit_at == NOW + timedelta(minutes=10)
    assert after_tp1.realized_r == pytest.approx(0.492, abs=1e-3)  # 0.5 x 6.45 / 6.55
    assert after_tp1.stop == pytest.approx(2650.15) and after_tp1.remaining == 0.5
    done = apply_quote(after_tp1, signal, quote(30, 2663.1), settings)
    assert done.status == "tp2" and done.exit_price == pytest.approx(2663.1)
    assert done.r_multiple == pytest.approx(1.481, abs=1e-3)  # less than 1.5R: costs are real
    assert done.duration_minutes == 30.0


def test_runner_stopped_at_breakeven_keeps_the_tp1_gain(settings: Settings) -> None:
    signal = buy(settings)
    done = run(signal, settings, quote(0, 2649.9), quote(10, 2656.6), quote(20, 2650.0))
    assert done.status == "breakeven"
    assert done.r_multiple == pytest.approx(0.477, abs=1e-3)  # 0.492 minus slippage on the rest


def test_stop_loss_and_a_gap_through_it(settings: Settings) -> None:
    signal = buy(settings)
    stopped = run(signal, settings, quote(0, 2649.9), quote(5, 2643.5))
    assert stopped.status == "sl" and stopped.r_multiple == pytest.approx(-1.023, abs=1e-3)
    gapped = run(signal, settings, quote(0, 2649.9), quote(5, 2638.0))
    assert gapped.r_multiple == pytest.approx(-1.863, abs=1e-3)  # filled where the market was


def test_stop_is_checked_before_the_target_on_a_wild_quote(settings: Settings) -> None:
    signal = buy(settings)
    wild = Quote(NOW + timedelta(minutes=1), 2643.0, 2670.0)  # bid below SL, ask above TP
    assert run(signal, settings, quote(0, 2649.9), wild).status == "sl"


def test_expiry_before_tp1_and_max_hold_after_it(settings: Settings) -> None:
    signal = buy(settings)
    expired = run(signal, settings, quote(0, 2649.9), quote(120, 2652.0))
    assert expired.status == "expired"
    assert expired.r_multiple == pytest.approx(0.275, abs=1e-3)
    still_open = run(signal, settings, quote(0, 2649.9), quote(10, 2656.6), quote(200, 2655.0))
    assert still_open.closed_at is None  # validity no longer applies once TP1 is in
    held = apply_quote(still_open, signal, quote(24 * 60 + 1, 2655.0), settings)
    assert held.status == "tp1" and held.r_multiple is not None and held.r_multiple > 0.6


def test_excursions_and_out_of_order_quotes(settings: Settings) -> None:
    signal = buy(settings)
    outcome = run(signal, settings, quote(0, 2649.9), quote(2, 2646.875), quote(4, 2653.425))
    assert outcome.mae_r == pytest.approx(0.5) and outcome.mfe_r == pytest.approx(0.5)
    late = apply_quote(outcome, signal, quote(3, 2640.0), settings)  # older than the last quote
    assert late == outcome


def test_sell_mirrors_buy(settings: Settings) -> None:
    signal = buy(settings).model_copy(
        update={
            "action": "SELL",
            "stop_loss": 2656.6,
            "tp1": 2643.6,
            "tp2": 2637.1,
            "price_source": "mt5",
        }
    )
    opened = open_position(signal, quote(0, 2649.9), settings)
    assert opened.entry_price == pytest.approx(2649.85)  # sell at the bid minus slippage
    done = run(signal, settings, quote(0, 2649.9), quote(5, 2643.3), quote(9, 2636.8))
    assert done.status == "tp2" and done.r_multiple is not None and done.r_multiple > 1.4


def test_cancel_closes_at_market(settings: Settings) -> None:
    signal = buy(settings)
    opened = run(signal, settings, quote(0, 2649.9))
    cancelled = close_at_market(opened, signal, quote(3, 2651.0), settings, "cancelled")
    assert cancelled.status == "cancelled" and cancelled.closed_at == NOW + timedelta(minutes=3)
    assert cancelled.r_multiple == pytest.approx((2650.95 - 2650.15) / 6.55, abs=1e-3)

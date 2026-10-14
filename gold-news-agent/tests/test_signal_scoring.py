"""Composite score, confidence, levels and position size (sections 10.1–10.4)."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.models import Level, MarketContext
from gold_agent.signals.levels import compute_levels, position_size
from gold_agent.signals.scoring import (
    confidence,
    macro_component,
    news_contributions,
    price_component,
    score,
)
from tests.builders import T0, analysis, flat_prices, market

# ------------------------------------------------------------------ news component


def test_news_weight_halves_every_half_life_and_ignores_future_or_stale_stories() -> None:
    story = analysis(half_life=120)  # impact 5, confidence 0.9, reliability 0.9 -> 0.81
    assert news_contributions([story], T0)[0].value == pytest.approx(0.81)
    later = news_contributions([story], T0 + timedelta(minutes=120))[0].value
    assert later == pytest.approx(0.405)
    assert news_contributions([story], T0 + timedelta(minutes=481)) == []
    assert news_contributions([story], T0 - timedelta(seconds=1)) == []  # no lookahead in replay


def test_bearish_priced_in_repeat_news_counts_less_and_neutral_counts_nothing() -> None:
    fresh = news_contributions([analysis(direction_gold="bearish")], T0)[0].value
    repeat = news_contributions(
        [analysis(direction_gold="bearish", priced_in_risk="high", is_new_information=False)], T0
    )[0].value
    assert fresh == pytest.approx(-0.81)
    assert repeat == pytest.approx(fresh * 0.5 * 0.5)
    assert news_contributions([analysis(direction_gold="neutral")], T0) == []
    assert news_contributions([analysis(is_gold_relevant=False)], T0) == []


def test_unconfirmed_story_moves_the_score_but_not_the_confirmed_only_score(
    settings: Settings,
) -> None:
    rumour = analysis(needs_confirmation=True)
    context = market(trend_h1="flat", trend_h4="flat", rsi_h1=50.0)
    breakdown = score(
        [rumour],
        context,
        weights=settings.signal_engine.weights,
        now=T0 + timedelta(minutes=5),
        price_at=flat_prices(2645.0),
    )
    assert breakdown.components.N > 0.6
    assert breakdown.dominant is not None and breakdown.dominant_confirmed is None
    assert breakdown.n_confirmed == 0.0
    # Without a confirmed story the price check falls back to plain momentum (half weight).
    assert breakdown.composite_confirmed_only == pytest.approx(
        0.2 * 0.5 * math.tanh(5.0 / 4.0), abs=1e-3
    )
    assert breakdown.composite > 2 * breakdown.composite_confirmed_only


# ------------------------------------------------------------------ macro / technical / price


def test_strong_dollar_and_rising_real_yields_are_bearish_for_gold() -> None:
    value, available = macro_component(market(dxy_z_1h=2.0, dxy_z_15m=2.0))
    assert available and value == pytest.approx(-math.tanh(1.0))
    falling_yields, _ = macro_component(market(real_yield_change_1d=-0.10))
    assert falling_yields == pytest.approx(math.tanh(2.0))
    nominal_only, _ = macro_component(market(us10y_change_1d=0.05))
    assert nominal_only == pytest.approx(-math.tanh(1.0))  # nominal stands in for real
    assert macro_component(market()) == (0.0, False)


def test_price_check_measures_the_move_since_the_news_in_atr_units() -> None:
    context = market(mid=2650.0, atr_m15=4.0)
    story = news_contributions([analysis()], T0)[0]
    anchored, move = price_component(context, story, flat_prices(2640.0), T0)
    assert move == pytest.approx(10.0)
    assert anchored == pytest.approx(math.tanh(2.5))
    momentum, _ = price_component(context, None, flat_prices(2640.0), T0)
    assert momentum == pytest.approx(0.5 * math.tanh(2.5))
    assert price_component(context, story, flat_prices(None), T0)[0] == 0.0


def test_closed_market_scores_zero_and_has_no_confidence(settings: Settings) -> None:
    weights = settings.signal_engine.weights
    closed = market(market_open=False, dxy_z_1h=-2.0)
    breakdown = score([analysis()], closed, weights=weights, now=T0, price_at=flat_prices(2640.0))
    assert breakdown.composite == 0.0
    assert confidence(breakdown, closed, weights) == 0


def test_confidence_rewards_agreement_and_penalises_weak_data(settings: Settings) -> None:
    weights = settings.signal_engine.weights
    now = T0 + timedelta(minutes=5)
    aligned = market(dxy_z_1h=-1.5, dxy_z_15m=-1.0)
    split = market(dxy_z_1h=2.5, dxy_z_15m=2.5, trend_h1="down", trend_h4="down", rsi_h1=35.0)

    def conf(context: MarketContext, **story: Any) -> int:
        breakdown = score(
            [analysis(first_seen=T0, **story)],
            context,
            weights=weights,
            now=now,
            price_at=flat_prices(2645.0),
        )
        return confidence(breakdown, context, weights)

    assert conf(aligned) == 100
    assert conf(split) < 50
    assert conf(aligned.model_copy(update={"proxy": True})) == 90
    assert conf(aligned, needs_confirmation=True) < conf(aligned)
    assert conf(aligned.model_copy(update={"stale": True})) == 0


# ------------------------------------------------------------------ levels + size


def test_buy_stop_sits_beyond_the_swing_low_and_targets_follow_r_multiples(
    settings: Settings,
) -> None:
    levels = [
        Level(name="swing low H1", price=2644.0, kind="support"),
        Level(name="high kemarin", price=2668.0, kind="resistance"),
    ]
    trade = compute_levels("BUY", market(levels=levels), settings.signal_engine.levels)
    assert trade is not None
    assert trade.entry == pytest.approx(2650.1)  # buy at the ask
    assert trade.sl_distance == pytest.approx(6.5)  # swing 6.1 below + 0.1 ATR buffer > 1.2 ATR
    assert trade.stop_loss == pytest.approx(2643.6)
    assert (trade.tp1, trade.tp2) == (pytest.approx(2656.6), pytest.approx(2663.1))
    assert (trade.tp3, trade.tp3_source) == (2668.0, "high kemarin")
    assert trade.risk_reward == pytest.approx(2.0)
    assert (trade.entry_low, trade.entry_high) == (pytest.approx(2649.6), pytest.approx(2650.4))


def test_sell_uses_the_atr_floor_when_no_structure_is_near(settings: Settings) -> None:
    far_swing = [Level(name="swing high H1", price=2680.0, kind="resistance")]
    trade = compute_levels("SELL", market(levels=far_swing), settings.signal_engine.levels)
    assert trade is not None
    assert trade.entry == pytest.approx(2649.9)  # sell at the bid
    assert trade.sl_distance == pytest.approx(4.8)  # 1.2 x ATR(M15) 4.0; 30$ swing is ignored
    assert trade.stop_loss == pytest.approx(2654.7)
    assert trade.tp2 == pytest.approx(2640.3)
    assert (trade.tp3, trade.tp3_source) == (pytest.approx(2635.5), "3R")
    assert compute_levels("SELL", market(atr_m15=None), settings.signal_engine.levels) is None


def test_lot_size_is_never_guessed(settings: Settings) -> None:
    risk = settings.risk
    assert risk.account_equity is None
    lots, note = position_size(sl_distance=6.0, risk=risk, contract_size=100, volume_step=0.01)
    assert lots is None and note and "account_equity" in note
    funded = risk.model_copy(update={"account_equity": 10_000.0})
    lots, note = position_size(sl_distance=6.0, risk=funded, contract_size=None, volume_step=None)
    assert lots is None and note and "contract size" in note
    # $10,000 x 0.5% = $50 risk; $6 stop x 100 oz = $600 per lot -> 0.083 -> 0.08 (rounded down)
    assert position_size(sl_distance=6.0, risk=funded, contract_size=100, volume_step=0.01) == (
        0.08,
        None,
    )
    tiny, note = position_size(sl_distance=60.0, risk=funded, contract_size=100, volume_step=0.1)
    assert tiny == 0.0 and note and "lot minimum" in note

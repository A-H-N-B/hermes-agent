"""Signal engine decisions (10.3–10.5): filters, hysteresis, cooldown, blackout and the schema."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.models import Analysis, CalendarEvent, Level, MarketContext, Signal
from gold_agent.signals.engine import Decision, EngineState, SignalEngine, wib_day
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, event, flat_prices, market

NOW = T0 + timedelta(minutes=5)
LEVELS = [
    Level(name="swing low H1", price=2644.0, kind="support"),
    Level(name="high kemarin", price=2668.0, kind="resistance"),
]


@pytest.fixture
def engine(settings: Settings) -> SignalEngine:
    return SignalEngine(settings, SessionClock(settings.sessions), config_version="cfg123")


def bullish_market(ts: datetime = NOW, **extra: Any) -> MarketContext:
    base: dict[str, Any] = {"dxy_z_1h": -1.5, "dxy_z_15m": -1.0, "levels": LEVELS}
    return market(ts=ts, **(base | extra))


def bearish_market(ts: datetime, **extra: Any) -> MarketContext:
    base: dict[str, Any] = {"trend_h1": "down", "trend_h4": "flat", "rsi_h1": 45.0}
    return market(mid=2645.0, ts=ts, levels=LEVELS, **(base | extra))


def run(
    engine: SignalEngine,
    *,
    now: datetime = NOW,
    analyses: Sequence[Analysis] | None = None,
    context: MarketContext | None = None,
    events: Sequence[CalendarEvent] = (),
    state: EngineState | None = None,
    then: float = 2645.0,
) -> Decision:
    return engine.evaluate(
        now=now,
        context=context or bullish_market(now),
        analyses=[analysis()] if analyses is None else analyses,
        events=events,
        state=state or EngineState(),
        price_at=flat_prices(then),
    )


def buy_signal(engine: SignalEngine) -> Signal:
    decision = run(engine)
    assert decision.signal is not None
    return decision.signal


# ------------------------------------------------------------------ the happy path


def test_aligned_evidence_produces_a_complete_buy_signal(engine: SignalEngine) -> None:
    decision = run(engine)
    signal = decision.signal
    assert signal is not None and decision.state.action == "BUY"
    assert decision.state.active_signal_id == signal.signal_id
    assert (signal.entry_low, signal.entry_high) == (2649.6, 2650.4)
    assert (signal.stop_loss, signal.tp1, signal.tp2, signal.tp3) == (
        2643.6,
        2656.6,
        2663.1,
        2668.0,
    )
    assert signal.valid_until_utc == NOW + timedelta(minutes=120)  # intraday validity
    assert signal.created_at_wib == "Selasa 13 Okt 19:35 WIB"
    assert signal.valid_until_wib == "Selasa 13 Okt 21:35 WIB"
    assert signal.session == "London–New York"
    assert signal.setup_type == "news_momentum"
    assert signal.confidence == 100 and signal.composite_score > 0.6
    assert signal.top_reasons[0].startswith("Berita: Serangan rudal")
    assert len(signal.top_reasons) == 3
    assert "harga menembus SL 2643.60" in signal.invalidation
    assert signal.lot_size is None and signal.lot_note and "account_equity" in signal.lot_note
    assert signal.linked_news_ids == ["c1"] and signal.first_seen_at == T0
    assert signal.trigger_source == "wire"
    assert signal.config_hash == "cfg123"
    assert signal.model_versions["triage"] == "claude-haiku-5-5@abc123"
    assert (signal.bid, signal.ask, signal.price_source) == (2649.9, 2650.1, "mt5")


def test_upcoming_release_is_listed_and_can_invalidate_the_signal(engine: SignalEngine) -> None:
    release = event("Retail Sales m/m", at=NOW + timedelta(hours=1), key="us_retail_sales_mom")
    signal = run(engine, events=[release]).signal
    assert signal is not None
    assert signal.upcoming_events_4h == ["20:35 WIB USD Retail Sales m/m (forecast 0.3%)"]
    assert any("Retail Sales m/m pukul 20:35 WIB" in text for text in signal.invalidation)


# ------------------------------------------------------------------ never trade unconfirmed news


def test_single_source_claim_never_opens_a_position_until_confirmed(
    engine: SignalEngine,
) -> None:
    context = bullish_market(trend_h4="flat", rsi_h1=55.0)
    rumour = analysis(needs_confirmation=True)
    waiting = run(engine, analyses=[rumour], context=context)
    assert waiting.signal is None and waiting.state.action == "WAIT"
    assert waiting.breakdown.composite >= 0.35  # strong enough on its face ...
    assert waiting.state.wait_reasons == [
        "menunggu konfirmasi berita (sumber tunggal belum terverifikasi)"
    ]
    assert waiting.state.reasons[0].startswith("Berita (belum terkonfirmasi):")
    confirmed = run(engine, analyses=[analysis()], context=context)
    assert confirmed.signal is not None and confirmed.signal.action == "BUY"


# ------------------------------------------------------------------ event blackout


def test_high_impact_release_blacks_out_entries_until_price_confirms(
    engine: SignalEngine,
) -> None:
    cpi = event(at=NOW + timedelta(minutes=10))
    before = run(engine, events=[cpi])
    assert before.signal is None
    assert before.state.wait_reasons == ["rilis USD CPI m/m dalam 10 mnt"]
    just_after = run(engine, now=cpi.scheduled_at + timedelta(seconds=30), events=[cpi])
    assert just_after.state.wait_reasons == ["menunggu konfirmasi harga setelah rilis CPI m/m"]
    settled = run(engine, now=cpi.scheduled_at + timedelta(seconds=61), events=[cpi])
    assert settled.signal is not None
    medium = event(at=NOW + timedelta(minutes=10), impact="medium")
    assert run(engine, events=[medium]).signal is not None


def test_a_parsed_release_also_starts_the_confirmation_window(engine: SignalEngine) -> None:
    state = EngineState(last_release_at=NOW - timedelta(seconds=20), last_release_name="NFP")
    decision = run(engine, state=state)
    assert decision.state.wait_reasons == ["menunggu konfirmasi harga setelah rilis NFP"]


def test_news_trading_switch_lifts_the_blackout(settings: Settings) -> None:
    trading = settings.trading.model_copy(update={"news_trading": True})
    engine = SignalEngine(
        settings.model_copy(update={"trading": trading}),
        SessionClock(settings.sessions),
        config_version="x",
    )
    assert run(engine, events=[event(at=NOW + timedelta(minutes=10))]).signal is not None


# ------------------------------------------------------------------ hysteresis + cooldown


def test_reversal_needs_a_stronger_score_and_a_minimum_age(engine: SignalEngine) -> None:
    buy = buy_signal(engine)
    state = EngineState()
    state.record_signal(buy)
    later = buy.created_at_utc + timedelta(minutes=20)
    bearish = [analysis("c2", first_seen=later - timedelta(minutes=5), direction_gold="bearish")]

    weak = run(
        engine, now=later, analyses=bearish, context=bearish_market(later), state=state, then=2650.0
    )
    assert -0.5 < weak.breakdown.composite <= -0.35
    assert weak.signal is None
    assert weak.state.action == "BUY" and weak.state.active_signal_id == buy.signal_id
    assert weak.state.wait_reasons[0].startswith("pembalikan arah butuh |S| ≥ 0.50")

    strong_context = bearish_market(later, dxy_z_1h=1.5, dxy_z_15m=1.0)
    flipped = run(
        engine, now=later, analyses=bearish, context=strong_context, state=state, then=2650.0
    )
    assert flipped.signal is not None and flipped.signal.action == "SELL"
    assert flipped.replaced is not None and flipped.replaced.signal_id == buy.signal_id

    too_soon = buy.created_at_utc + timedelta(minutes=10)
    early = run(
        engine,
        now=too_soon,
        analyses=[
            analysis("c2", first_seen=too_soon - timedelta(minutes=5), direction_gold="bearish")
        ],
        context=bearish_market(too_soon, dxy_z_1h=1.5, dxy_z_15m=1.0),
        state=state,
        then=2650.0,
    )
    assert early.signal is None and "pembalikan" in early.state.wait_reasons[0]


def test_same_direction_keeps_the_active_signal_and_cooldown_follows_a_close(
    engine: SignalEngine,
) -> None:
    buy = buy_signal(engine)
    state = EngineState()
    state.record_signal(buy)
    again = run(engine, now=NOW + timedelta(minutes=1), state=state)
    assert again.signal is None and again.state.action == "BUY"
    assert again.state.active_signal_id == buy.signal_id

    state.closed_signal_ids.add(buy.signal_id)  # e.g. stopped out by the paper tracker
    cooling = run(engine, now=NOW + timedelta(minutes=5), state=state)
    assert cooling.signal is None and cooling.state.action == "WAIT"
    assert cooling.state.wait_reasons == ["cooldown 10 mnt sejak sinyal terakhir"]
    assert run(engine, now=NOW + timedelta(minutes=11), state=state).signal is not None


def test_expired_signal_is_no_longer_active(engine: SignalEngine) -> None:
    buy = buy_signal(engine)
    state = EngineState()
    state.record_signal(buy)
    assert state.active(buy.valid_until_utc - timedelta(seconds=1)) is buy
    assert state.active(buy.valid_until_utc) is None


# ------------------------------------------------------------------ risk limits and guards


def test_daily_loss_and_signal_count_limits_stop_new_entries(engine: SignalEngine) -> None:
    today = wib_day(NOW)
    lost = run(engine, state=EngineState(daily_r=-6.0, daily_r_day=today))
    assert lost.state.wait_reasons == ["batas rugi harian tercapai (3.0%)"]
    yesterday = run(engine, state=EngineState(daily_r=-6.0, daily_r_day="2026-10-12"))
    assert yesterday.signal is not None
    busy = run(engine, state=EngineState(signals_today=6, signals_day=today))
    assert busy.state.wait_reasons == ["batas 6 sinyal per hari tercapai"]


@pytest.mark.parametrize(
    ("label", "now", "context_update", "halted", "reason"),
    [
        ("spread", NOW, {"spread": 0.8, "bid": 2649.6, "ask": 2650.4}, False, "spread melebar"),
        ("stale", NOW, {"stale": True, "tick_age_seconds": 42.0}, False, "data harga basi (42"),
        (
            "weekend",
            datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
            {},
            False,
            "pasar tutup: akhir pekan",
        ),
        (
            "daily break",
            datetime(2026, 10, 13, 21, 30, tzinfo=UTC),  # 17:30 New York
            {},
            False,
            "pasar tutup: jeda harian",
        ),
        ("halted", NOW, {}, True, "kill switch (/stop)"),
    ],
)
def test_market_guards_force_wait(
    engine: SignalEngine,
    label: str,
    now: datetime,
    context_update: dict[str, Any],
    halted: bool,
    reason: str,
) -> None:
    context = bullish_market(now).model_copy(update=context_update)
    stories = [analysis(first_seen=now - timedelta(minutes=5))]
    decision = run(
        engine, now=now, analyses=stories, context=context, state=EngineState(halted=halted)
    )
    assert decision.signal is None, label
    assert decision.state.action == "WAIT"
    assert any(reason in text for text in decision.state.wait_reasons), decision.state.wait_reasons
    assert decision.state.halted is halted


def test_news_and_price_pulling_hard_apart_means_wait(engine: SignalEngine) -> None:
    context = bullish_market(dxy_z_1h=-3.0, dxy_z_15m=-3.0)
    decision = run(engine, context=context, then=2656.0)  # bullish news, price fell $6
    assert decision.breakdown.components.N >= 0.5 and decision.breakdown.components.P <= -0.5
    assert "arah berita dan pergerakan harga bertentangan kuat" in decision.state.wait_reasons


# ------------------------------------------------------------------ persistence


def test_engine_state_round_trips_through_json(engine: SignalEngine) -> None:
    buy = buy_signal(engine)
    state = EngineState(last_release_at=NOW, last_release_name="CPI m/m")
    state.record_signal(buy)
    state.closed_signal_ids.add("sig-old")
    restored = EngineState.from_json(state.to_json())
    assert restored.last_signal == buy
    assert restored.count_today(NOW) == 1
    assert restored.closed_signal_ids == {"sig-old"}
    assert restored.last_release_at == NOW and restored.last_release_name == "CPI m/m"
    next_day = NOW + timedelta(days=1)
    assert restored.count_today(next_day) == 0

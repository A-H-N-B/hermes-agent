"""Notifier end to end: bus messages -> policy -> dispatcher -> (fake) Telegram."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.bus import Message
from gold_agent.core.clock import ManualClock
from gold_agent.econ_calendar.events import load_events
from gold_agent.econ_calendar.playbook import build_playbook
from gold_agent.evaluation.performance import summarize
from gold_agent.models import Alert, Level, Outlook, ReleaseReading, Signal, SignalOutcome
from gold_agent.services.notifier import NotifierService
from gold_agent.signals.engine import EngineState, SignalEngine
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, event, flat_prices, market
from tests.test_dispatcher import FakeSender

NOW = T0 + timedelta(minutes=5)


def msg(payload: Any) -> Message:
    return Message(id="1-0", stream="x", data=payload.model_dump(mode="json"))


@pytest.fixture
async def notifier(ctx: AppContext, clock: ManualClock) -> tuple[NotifierService, FakeSender]:
    clock.set(NOW)
    sender = FakeSender()
    service = NotifierService(ctx, sender=sender, poll=False)
    await service.dispatcher.load()
    return service, sender


def make_signal(settings: Settings, when: Any = NOW, action: str = "bullish") -> Signal:
    engine = SignalEngine(settings, SessionClock(settings.sessions), config_version="x")
    decision = engine.evaluate(
        now=when,
        context=market(
            ts=when,
            dxy_z_1h=-1.5 if action == "bullish" else 1.5,
            dxy_z_15m=-1.0 if action == "bullish" else 1.0,
            trend_h1="up" if action == "bullish" else "down",
            trend_h4="up" if action == "bullish" else "down",
            rsi_h1=60.0 if action == "bullish" else 40.0,
            mid=2650.0 if action == "bullish" else 2640.0,
            levels=[Level(name="swing low H1", price=2644.0, kind="support")],
        ),
        analyses=[analysis(first_seen=when - timedelta(minutes=5), direction_gold=action)],
        events=[],
        state=EngineState(),
        price_at=flat_prices(2645.0),
    )
    assert decision.signal is not None
    return decision.signal


async def test_each_kind_of_event_gets_its_own_treatment(
    notifier: tuple[NotifierService, FakeSender], settings: Settings
) -> None:
    service, sender = notifier
    signal = make_signal(settings)
    await service.handle_signal(msg(signal))
    assert sender.sent[-1].startswith("🟢 <b>BUY XAUUSD</b>")

    await service.handle_analysis(msg(analysis("c2", first_seen=NOW, impact_score=4)))
    assert sender.sent[-1].startswith("🔴 <b>BERITA PENTING</b>")
    fast = analysis("c3", first_seen=NOW, impact_score=5).model_copy(update={"origin": "fast_path"})
    small = analysis("c4", first_seen=NOW, impact_score=2)
    for story in (fast, small):
        await service.handle_analysis(msg(story))
    assert len(sender.sent) == 2  # fast path already flashed; impact 2 stays on the dashboard

    release = ReleaseReading(
        event_key="us_cpi_mom",
        event_name="CPI m/m",
        headline="US CPI 0.5% vs 0.3%",
        actual=0.5,
        forecast=0.3,
        unit="%",
        surprise=0.2,
        z=2.0,
        bucket="strong_bearish",
        direction_gold="bearish",
        first_seen_at=NOW - timedelta(seconds=3),
        parsed_at=NOW - timedelta(seconds=3),
    )
    await service.handle_release(msg(release))
    await service.handle_release(msg(release))  # a second source with the same release
    assert sum(text.startswith("🔴 <b>FLASH</b>") for text in sender.sent) == 1

    spec = load_events().by_key("us_cpi_mom")
    assert spec is not None
    cpi = event(at=NOW + timedelta(minutes=30))
    await service.handle_playbook(
        msg(build_playbook(cpi, spec, surprise_std=0.1, z_basis="prior", now=NOW))
    )
    assert sender.sent[-1].startswith("📋 <b>PLAYBOOK</b>")

    spike = Alert(
        id="a1",
        kind="volatility",
        severity="warning",
        title="Lonjakan harga: naik $6.00 dalam 1 menit",
        detail="Penyebab belum diketahui.",
        created_at=NOW,
        dedupe_key="spike:move:up",
    )
    await service.handle_alert(msg(spike))
    assert sender.sent[-1].startswith("⚡ <b>Lonjakan harga")


async def test_plain_hourly_outlook_stays_off_telegram(
    notifier: tuple[NotifierService, FakeSender],
) -> None:
    service, sender = notifier
    await service.handle_outlook(msg(Outlook(id="o1", trigger="hourly", created_at=NOW, rows=[])))
    assert sender.sent == []
    london = Outlook(id="o2", trigger="pembukaan sesi London", created_at=NOW, rows=[])
    await service.handle_outlook(msg(london))
    assert sender.sent[-1].startswith("🗓️ <b>OUTLOOK 24 JAM</b>")


async def test_late_items_are_not_sent_after_a_backlog(
    notifier: tuple[NotifierService, FakeSender], clock: ManualClock, settings: Settings
) -> None:
    service, sender = notifier
    signal = make_signal(settings)
    clock.set(signal.valid_until_utc + timedelta(minutes=1))
    await service.handle_signal(msg(signal))
    await service.handle_analysis(msg(analysis("old", first_seen=NOW, impact_score=5)))
    assert sender.sent == []


async def test_reversal_announces_the_cancelled_signal(
    notifier: tuple[NotifierService, FakeSender], settings: Settings, ctx: AppContext
) -> None:
    service, sender = notifier
    buy = make_signal(settings)
    sell = make_signal(settings, NOW + timedelta(minutes=20), action="bearish")
    for signal in (buy, sell):
        await ctx.signals.save_signal(signal)
    await service.handle_event(
        Message(
            id="1-0",
            stream="app.events",
            data={"type": "signal.replaced", "signal_id": buy.signal_id, "by": sell.signal_id},
        )
    )
    assert sender.sent[-1].startswith("♻️ <b>Sinyal BUY dibatalkan</b>")
    assert "Arah berbalik menjadi SELL" in sender.sent[-1]


# ------------------------------------------------------------------ performance numbers


def outcome(signal: Signal, r: float, minutes: int) -> SignalOutcome:
    return SignalOutcome(
        signal_id=signal.signal_id,
        status="tp2" if r > 0 else "sl",
        entry_price=signal.ask,
        opened_at=signal.created_at_utc,
        closed_at=signal.created_at_utc + timedelta(minutes=minutes),
        r_multiple=r,
    )


def test_performance_reports_intervals_drawdown_and_small_samples(settings: Settings) -> None:
    signals = [make_signal(settings, NOW + timedelta(hours=h)) for h in range(5)]
    results = [2.0, -1.0, -1.0, 1.5, -1.0]
    outcomes = [outcome(s, r, 30) for s, r in zip(signals, results, strict=True)]
    perf = summarize(signals, outcomes)
    assert (perf.n_closed, perf.wins) == (5, 2)
    assert perf.win_rate == pytest.approx(0.4)
    assert perf.wilson[0] < 0.4 < perf.wilson[1]
    assert perf.avg_r == pytest.approx(0.1)
    assert perf.total_r == pytest.approx(0.5)
    assert perf.max_drawdown_r == pytest.approx(2.0)  # +2 -> 0 peak-to-trough
    assert perf.expectancy_ci is not None
    assert not perf.enough
    assert perf.notes[0].startswith("sampel belum cukup (5 sinyal tertutup, butuh ≥ 30)")
    assert [g.label for g in perf.by_category] == ["geopolitics"]
    assert perf.calibration[0].label == "90–100"

"""Signal service end to end: MemoryBus + SQLite + ManualClock + a scripted Claude client."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.bus import (
    STREAM_EVENTS,
    STREAM_OUTLOOK,
    STREAM_SIGNALS,
    STREAM_STATE,
    Message,
)
from gold_agent.core.clock import ManualClock
from gold_agent.models import Candle, Level, MarketContext, ReleaseReading
from gold_agent.services.signal import KV_ENGINE, KV_HALT, PriceHistory, SignalService
from gold_agent.signals.outlook import OutlookNarrative
from tests.builders import T0, analysis, market
from tests.fakes import FakeAnthropic, parsed, timeout_error

NOW = T0 + timedelta(minutes=5)
LEVELS = [
    Level(name="swing low H1", price=2644.0, kind="support"),
    Level(name="high kemarin", price=2668.0, kind="resistance"),
]

Factory = Callable[..., Any]


@pytest.fixture
async def service_factory(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> AsyncIterator[Factory]:
    contexts: list[AppContext] = []

    async def make(
        *script: Any, at: datetime = T0, settings: Settings | None = None
    ) -> tuple[SignalService, AppContext]:
        # Same SQLite file on every call: a second call is a restart.
        context = ctx_factory(settings_override=settings)
        await context.db.create_all()
        contexts.append(context)
        clock.set(at)
        service = SignalService(context, anthropic_client=FakeAnthropic(*script))
        await service.load()
        return service, context

    yield make
    for context in contexts:
        await context.close()


def msg(stream: str, payload: Any) -> Message:
    data = payload if isinstance(payload, dict) else payload.model_dump(mode="json")
    return Message(id="1-0", stream=stream, data=data)


async def drain(context: AppContext, stream: str) -> list[dict[str, Any]]:
    messages = await context.bus.read(stream, f"test-{stream}", "t", count=500, block_ms=0)
    return [m.data for m in messages]


def bullish(ts: datetime, mid: float) -> MarketContext:
    return market(mid=mid, ts=ts, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=LEVELS)


async def feed_rally(service: SignalService, clock: ManualClock) -> None:
    """Price climbs from 2645 at T0 to 2650 at NOW, one snapshot a minute."""
    for minute in range(6):
        clock.set(T0 + timedelta(minutes=minute))
        ts = clock.now()
        await service.handle_context(msg("market.context", bullish(ts, 2645.0 + minute)))


# ------------------------------------------------------------------ signal path


async def test_news_with_confirming_price_publishes_and_persists_one_signal(
    service_factory: Factory, clock: ManualClock
) -> None:
    service, context = await service_factory()
    await feed_rally(service, clock)
    story = analysis(first_seen=T0)
    await context.news.save_analysis(story)  # what the analyzer does before publishing
    await service.handle_analysis(msg("news.analyses", story))

    signals = await drain(context, STREAM_SIGNALS)
    assert [s["action"] for s in signals] == ["BUY"]
    stored = await context.signals.latest_signals()
    assert [s.signal_id for s in stored] == [signals[0]["signal_id"]]
    states = await drain(context, STREAM_STATE)
    assert states[-1]["action"] == "BUY"
    kv = await context.ops.kv_get(KV_ENGINE)
    assert kv is not None and kv["signals_today"] == 1
    latency = await context.ops.latency_samples(T0)
    assert [(s.stage, s.seconds_from_first_seen) for s in latency] == [("signal", 300.0)]
    events = await drain(context, STREAM_EVENTS)
    assert {"type": "outlook.refresh", "reason": "sinyal BUY baru"}.items() <= events[-1].items()

    # Restart: the active signal and the daily count survive, the same evidence is not re-sent.
    restarted, context2 = await service_factory(at=NOW + timedelta(minutes=1))
    assert restarted.state.last_signal is not None
    assert restarted.state.count_today(clock.now()) == 1
    await restarted.handle_context(msg("market.context", bullish(clock.now(), 2650.5)))
    assert await drain(context2, STREAM_SIGNALS) == []
    assert restarted.last_state is not None and restarted.last_state.action == "BUY"


async def test_stop_halts_signals_and_survives_a_restart(
    service_factory: Factory, clock: ManualClock
) -> None:
    service, context = await service_factory()
    await feed_rally(service, clock)
    await service.handle_event(msg(STREAM_EVENTS, {"type": "signal.halt", "halted": True}))
    await service.handle_analysis(msg("news.analyses", analysis(first_seen=T0)))
    assert await drain(context, STREAM_SIGNALS) == []
    state = service.last_state
    assert state is not None and state.halted and state.action == "WAIT"
    assert "sinyal dihentikan lewat kill switch (/stop)" in state.wait_reasons
    halt = await context.ops.kv_get(KV_HALT)
    assert halt is not None and halt["halted"] is True

    restarted, _ = await service_factory(at=NOW)
    assert restarted.state.halted


def without_llm(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"outlook": settings.outlook.model_copy(update={"use_llm": False})}
    )


async def test_silent_market_feed_turns_the_state_stale(
    service_factory: Factory, clock: ManualClock, settings: Settings
) -> None:
    service, _ = await service_factory(settings=without_llm(settings))
    await feed_rally(service, clock)
    clock.advance(120)  # the market service stopped publishing
    await service.tick()
    state = service.last_state
    assert state is not None and state.action == "WAIT"
    assert any(reason.startswith("data harga basi") for reason in state.wait_reasons)


async def test_a_parsed_release_holds_entries_for_the_confirmation_window(
    service_factory: Factory, clock: ManualClock
) -> None:
    service, context = await service_factory()
    await feed_rally(service, clock)
    release = ReleaseReading(
        event_key="us_cpi_mom",
        event_name="CPI m/m",
        headline="US CPI m/m 0.1% vs 0.3% expected",
        actual=0.1,
        forecast=0.3,
        unit="%",
        first_seen_at=NOW - timedelta(seconds=10),
        parsed_at=NOW - timedelta(seconds=10),
    )
    await service.handle_release(msg("news.releases", release))
    await service.handle_analysis(msg("news.analyses", analysis(first_seen=T0)))
    assert await drain(context, STREAM_SIGNALS) == []
    assert service.last_state is not None
    assert "menunggu konfirmasi harga setelah rilis CPI m/m" in service.last_state.wait_reasons
    clock.advance(60)
    await service.handle_context(msg("market.context", bullish(clock.now(), 2650.2)))
    assert [s["action"] for s in await drain(context, STREAM_SIGNALS)] == ["BUY"]


# ------------------------------------------------------------------ outlook scheduling


async def test_hourly_outlook_is_built_once_per_hour_with_an_ai_narrative(
    service_factory: Factory, clock: ManualClock, settings: Settings
) -> None:
    narrative = OutlookNarrative(narrative_id="Jam 19:30 WIB ada CPI; tunggu konfirmasi.")
    service, context = await service_factory(parsed(narrative), timeout_error())
    await feed_rally(service, clock)
    await service.scheduled_outlooks(clock.now())
    await service.scheduled_outlooks(clock.now() + timedelta(minutes=10))  # same hour
    outlooks = await drain(context, STREAM_OUTLOOK)
    assert len(outlooks) == 1
    first = outlooks[0]
    assert first["trigger"] == "pembukaan sesi New York"  # 08:00 New York falls in this hour
    assert first["narrative_id"] == narrative.narrative_id
    assert first["model"] == settings.llm.outlook.model
    assert len(first["rows"]) == 24

    restarted, context2 = await service_factory(at=clock.now() + timedelta(minutes=15))
    await restarted.scheduled_outlooks(clock.now())
    assert await drain(context2, STREAM_OUTLOOK) == []  # a restart does not resend this hour's

    next_hour = datetime(2026, 10, 13, 13, 0, 30, tzinfo=UTC)
    clock.set(next_hour)
    await service.scheduled_outlooks(next_hour)
    later = await drain(context, STREAM_OUTLOOK)
    assert [o["trigger"] for o in later] == ["hourly"]
    assert later[0]["narrative_id"] is None  # the LLM timed out: the table still goes out
    assert any("narasi AI tidak tersedia" in note for note in later[0]["notes"])


async def test_important_news_refreshes_the_outlook_at_most_every_few_minutes(
    service_factory: Factory, clock: ManualClock, settings: Settings
) -> None:
    service, context = await service_factory(settings=without_llm(settings))
    await feed_rally(service, clock)
    refresh = {"type": "outlook.refresh", "reason": "Serangan rudal"}
    await service.handle_event(msg(STREAM_EVENTS, refresh))
    clock.advance(60)
    await service.handle_event(msg(STREAM_EVENTS, refresh))
    clock.advance(5 * 60)
    await service.handle_event(msg(STREAM_EVENTS, refresh))
    triggers = [o["trigger"] for o in await drain(context, STREAM_OUTLOOK)]
    assert triggers == ["berita: Serangan rudal", "berita: Serangan rudal"]


async def test_weekend_builds_only_the_monday_open_outlook(
    service_factory: Factory, clock: ManualClock, settings: Settings
) -> None:
    sunday = datetime(2026, 10, 18, 9, 5, tzinfo=UTC)  # Sunday 16:05 WIB
    service, context = await service_factory(at=sunday, settings=without_llm(settings))
    await service.scheduled_outlooks(sunday)
    await service.scheduled_outlooks(sunday + timedelta(hours=1))
    outlooks = await drain(context, STREAM_OUTLOOK)
    assert [o["trigger"] for o in outlooks] == ["pembukaan Senin"]
    assert outlooks[0]["rows"][0]["label_wib"] == "05:00–06:00"
    assert outlooks[0]["notes"][0] == "Pasar buka Senin 19 Okt 05:00 WIB"


# ------------------------------------------------------------------ profile + price buffer


async def test_profile_is_rebuilt_from_stored_h1_bars(
    service_factory: Factory, ctx_factory: Callable[..., AppContext]
) -> None:
    seed = ctx_factory()
    await seed.db.create_all()
    start = datetime(2026, 9, 1, tzinfo=UTC)
    bars = [
        Candle(
            source="mt5",
            tf="H1",
            open_time=start + timedelta(hours=h),
            close_time=start + timedelta(hours=h + 1),
            open=2600.0,
            high=2606.0,
            low=2598.0,
            close=2604.0,
        )
        for h in range(24 * 30)
    ]
    await seed.market.upsert_candles(bars)
    await seed.close()
    service, context = await service_factory()
    assert not service.profile.empty
    stats, computed_at = await context.signals.profile()
    assert computed_at is not None and len(stats) == 72  # 24 buckets x 3 clocks
    assert service.profile.typical_range() == pytest.approx(8.0)


def test_price_history_answers_only_inside_its_window() -> None:
    history = PriceHistory(keep=timedelta(hours=1), max_gap=timedelta(minutes=5))
    for minute in range(0, 90, 1):
        history.add(T0 + timedelta(minutes=minute), 2600.0 + minute)
    assert len(history) == 61  # older than one hour is dropped
    assert history.at(T0 + timedelta(minutes=60, seconds=30)) == 2660.0
    assert history.at(T0 + timedelta(minutes=10)) is None  # before the window
    assert history.at(T0 + timedelta(minutes=96)) is None  # gap longer than five minutes
    history.add(T0 + timedelta(minutes=70, seconds=30), 1.0)  # out-of-order insert
    assert history.at(T0 + timedelta(minutes=70, seconds=40)) == 1.0

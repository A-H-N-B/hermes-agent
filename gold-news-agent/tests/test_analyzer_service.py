"""Analyzer service end to end: MemoryBus + SQLite + a scripted Claude client."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gold_agent.core.app import AppContext
from gold_agent.core.bus import (
    STREAM_ALERTS,
    STREAM_ANALYSES,
    STREAM_EVENTS,
    STREAM_RELEASES,
    Message,
)
from gold_agent.core.clock import ManualClock
from gold_agent.models import Analysis, CalendarEvent, MarketContext, NewsCluster
from gold_agent.services.analyzer import AnalyzerService
from tests.fakes import FakeAnthropic, parsed, timeout_error
from tests.test_llm_triage import triage_value

T0 = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)


def cluster(title: str, *, cid: str = "c1", **extra: Any) -> NewsCluster:
    base: dict[str, Any] = {
        "id": cid,
        "title": title,
        "lead_item_id": f"squawk:{cid}",
        "lead_source_id": "squawk",
        "item_ids": [f"squawk:{cid}"],
        "source_ids": ["squawk"],
        "first_seen_at": T0,
        "updated_at": T0,
        "tier": 1,
        "reliability": 0.8,
    }
    return NewsCluster.model_validate(base | extra)


@pytest.fixture
async def service_factory(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> AsyncIterator[Callable[..., Any]]:
    contexts: list[AppContext] = []

    async def make(*script: Any) -> tuple[AnalyzerService, AppContext, FakeAnthropic]:
        context = ctx_factory()
        await context.db.create_all()
        contexts.append(context)
        clock.set(T0 + timedelta(seconds=2))
        fake = FakeAnthropic(*script)
        return AnalyzerService(context, anthropic_client=fake), context, fake

    yield make
    for context in contexts:
        await context.close()


async def drain(context: AppContext, stream: str) -> list[Message]:
    return await context.bus.read(stream, f"test-{stream}", "t", count=100, block_ms=0)


async def test_relevant_story_is_triaged_published_and_timed(
    service_factory: Callable[..., Any],
) -> None:
    service, context, fake = await service_factory(parsed(triage_value()))
    result = await service.handle_cluster(cluster("Fed's Logan signals fewer rate cuts ahead"))
    assert result is not None and result.origin == "llm"
    (message,) = await drain(context, STREAM_ANALYSES)
    published = Analysis.model_validate(message.data)
    assert published.cluster_id == "c1" and published.triage.direction_gold == "bearish"
    stored = await context.news.get_analysis("c1")
    assert stored is not None and stored.model == "claude-haiku-5-5"
    stages = {s.stage: s for s in await context.ops.latency_samples(T0 - timedelta(minutes=1))}
    assert stages["triaged"].seconds_from_first_seen == pytest.approx(2.0)
    assert len(fake.requests) == 1


async def test_irrelevant_backfilled_and_stale_stories_never_reach_the_llm(
    service_factory: Callable[..., Any], clock: ManualClock
) -> None:
    service, context, fake = await service_factory()
    assert await service.handle_cluster(cluster("Apple unveils a new iPhone")) is None
    assert (
        await service.handle_cluster(cluster("Gold steady ahead of Fed", cid="c2", backfill=True))
        is None
    )
    clock.set(T0 + timedelta(hours=2))
    assert await service.handle_cluster(cluster("Gold jumps on Fed cut bets", cid="c3")) is None
    assert fake.requests == [] and await drain(context, STREAM_ANALYSES) == []
    stored = await context.news.get_cluster("c1")
    assert stored is not None and stored.prefilter is not None and not stored.prefilter.relevant


async def test_release_headline_takes_the_fast_path_and_fills_the_calendar(
    service_factory: Callable[..., Any],
) -> None:
    service, context, _ = await service_factory(timeout_error())
    event = CalendarEvent(
        id="cpi-oct",
        title="CPI m/m",
        country="USD",
        scheduled_at=T0,
        impact="high",
        forecast="0.3%",
        previous="0.2%",
        event_key="us_cpi_mom",
        source_id="ff_calendar_week",
    )
    await context.calendar.upsert_events([event], T0)
    result = await service.handle_cluster(cluster("US CPI MoM 0.5% vs 0.3% exp; prior 0.2%"))
    assert result is not None and result.origin == "fast_path"
    assert result.triage.direction_gold == "bearish" and result.triage.impact_score == 5
    (release,) = await drain(context, STREAM_RELEASES)
    assert release.data["bucket"] == "strong_bearish"
    stored_event = (
        await context.calendar.between(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    )[0]
    assert stored_event.actual == "0.5%"
    flash = [
        s
        for s in await context.ops.latency_samples(T0 - timedelta(minutes=1))
        if s.stage == "flash"
    ]
    assert (
        flash
        and flash[0].seconds_from_first_seen is not None
        and flash[0].seconds_from_first_seen < 5
    )
    events = await drain(context, STREAM_EVENTS)
    assert any(e.data["type"] == "outlook.refresh" for e in events)


async def test_second_source_upgrades_an_unconfirmed_big_claim(
    service_factory: Callable[..., Any],
) -> None:
    war = triage_value(
        category="geopolitics",
        event_type="breaking",
        direction_gold="bullish",
        evidence=["Missile strike"],
        impact_score=3,
    )
    service, context, _ = await service_factory(parsed(war))
    first = await service.handle_cluster(cluster("Missile strike on Iranian oil terminal"))
    assert first is not None and first.weight < 1 and first.triage.needs_confirmation
    update = cluster(
        "Missile strike on Iranian oil terminal",
        source_ids=["squawk", "reuters"],
        item_ids=["squawk:c1", "reuters:1"],
        is_new=False,
    )
    confirmed = await service.handle_cluster(update)
    assert confirmed is not None and confirmed.confirmed and confirmed.weight == 1.0
    published = [Analysis.model_validate(m.data) for m in await drain(context, STREAM_ANALYSES)]
    assert [a.confirmed for a in published] == [False, True]


async def test_llm_outage_degrades_to_rules_and_raises_one_health_alert(
    service_factory: Callable[..., Any],
) -> None:
    failures = 5
    service, context, _ = await service_factory(*[timeout_error() for _ in range(failures)])
    for n in range(failures + 2):
        result = await service.handle_cluster(
            cluster(f"Gold rallies as dollar slides, part {n}", cid=f"c{n}")
        )
        assert result is not None and result.origin == "rules"
    alerts = [m.data for m in await drain(context, STREAM_ALERTS)]
    assert [a["kind"] for a in alerts] == ["health"]
    assert "rules-only" in alerts[0]["title"]


def ctx_at(ts: datetime, mid: float) -> MarketContext:
    return MarketContext(
        ts=ts,
        mid=mid,
        bid=mid - 0.1,
        ask=mid + 0.1,
        spread=0.2,
        spread_median_30m=0.2,
        atr_m1=0.8,
        stale=False,
        market_open=True,
        session="overlap",
        liquidity_factor=1.0,
    )


async def test_unexplained_price_spike_triggers_search_and_later_attribution(
    service_factory: Callable[..., Any], clock: ManualClock
) -> None:
    service, context, _ = await service_factory(
        parsed(triage_value(category="geopolitics", direction_gold="bullish", evidence=[]))
    )
    for seconds, mid in ((0, 2650.0), (60, 2650.2), (120, 2655.0)):
        await service.handle_context(
            Message(
                id=str(seconds),
                stream="ctx",
                data=ctx_at(T0 + timedelta(seconds=seconds), mid).model_dump(mode="json"),
            )
        )
    alerts = [m.data for m in await drain(context, STREAM_ALERTS)]
    assert alerts and alerts[0]["kind"] == "volatility" and "belum diketahui" in alerts[0]["detail"]
    poll = [
        m.data
        for m in await drain(context, STREAM_EVENTS)
        if m.data["type"] == "collector.poll_now"
    ]
    assert poll and poll[0]["source_ids"] == ["google_news_gold"]
    clock.set(T0 + timedelta(seconds=150))
    late = cluster(
        "Explosions reported near Strait of Hormuz",
        cid="h1",
        first_seen_at=T0 + timedelta(seconds=150),
        updated_at=T0 + timedelta(seconds=150),
    )
    await service.handle_cluster(late)
    follow_up = [m.data for m in await drain(context, STREAM_ALERTS)]
    assert any("Penyebab" in a["title"] for a in follow_up)

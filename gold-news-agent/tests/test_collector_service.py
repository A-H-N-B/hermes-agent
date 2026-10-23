from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx

from gold_agent.collectors.telegram import TelegramCollector, message_external_id, message_url
from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_NEWS_RAW
from gold_agent.models import NewsCluster, NewsItem
from gold_agent.ops.health import HealthTracker
from gold_agent.processing.normalizer import Normalizer, title_hash
from gold_agent.services.collector import CollectorService
from tests.conftest import T0, make_source
from tests.test_source_check import rss


def item(source: str, title: str, seconds: float = 0) -> NewsItem:
    return NewsItem(
        id=f"{source}:{title}:{seconds}",
        source_id=source,
        title=title,
        first_seen_at=T0 + timedelta(seconds=seconds),
        published_at=T0 + timedelta(seconds=seconds - 4),
        raw_hash=title_hash(title),
        tier=1,
        reliability=0.8,
    )


async def test_ingest_publishes_new_and_confirmed_stories_and_archives_everything(
    ctx: AppContext,
) -> None:
    service = CollectorService(ctx)
    await service.ingest(item("squawk", "Fed holds rates steady"))
    await service.ingest(item("squawk", "Fed holds rates steady", 1))  # same source repeat
    await service.ingest(item("fed", "Fed holds rates steady", 5))  # confirmation
    messages = await ctx.bus.read(STREAM_NEWS_RAW, "t", "t", block_ms=0)
    clusters = [NewsCluster.model_validate(m.data) for m in messages]
    assert [c.confirmation_count for c in clusters] == [0, 1]
    stored = await ctx.news.items_between(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    assert len(stored) == 3
    stages = await ctx.ops.latency_samples(T0 - timedelta(minutes=1))
    published = [s for s in stages if s.stage == "published"]
    assert published and all(s.seconds_from_first_seen == -4.0 for s in published)


async def test_collector_service_runs_pollers_end_to_end(
    ctx_factory: Callable[..., AppContext],
) -> None:
    context = ctx_factory([make_source(id="feed", poll_seconds=10)])
    await context.db.create_all()
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        polls += 1
        return httpx.Response(200, content=rss(("Gold surges on Middle East strike", T0)))

    service = CollectorService(context, transport=httpx.MockTransport(handler))
    stop = asyncio.Event()
    task = asyncio.create_task(service.run(stop))
    messages = await context.bus.read(STREAM_NEWS_RAW, "t", "t", block_ms=3000)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    try:
        assert polls >= 1
        assert messages and NewsCluster.model_validate(messages[0].data).backfill
        health = await context.ops.source_health()
        assert health and health[0].status == "ok"
    finally:
        await context.close()


def test_telegram_message_helpers() -> None:
    assert message_url("financialjuice", 42) == "https://t.me/financialjuice/42"
    assert message_url(None, 42) is None
    edited = datetime(2026, 10, 13, 12, 31, tzinfo=UTC)
    assert message_external_id(-100, 42, None) == "-100:42"
    assert message_external_id(-100, 42, edited).startswith("-100:42:edit:")


async def test_telegram_collector_turns_messages_and_edits_into_items(ctx: AppContext) -> None:
    emitted: list[NewsItem] = []

    async def emit(news: NewsItem) -> None:
        emitted.append(news)

    source = make_source(id="tg", type="telegram", url=None, channels=["squawk"])
    collector = TelegramCollector(
        source,
        api_id=1,
        api_hash="x",
        session_path=ctx.secrets.model_config["env_file"],  # type: ignore[arg-type]
        normalizer=Normalizer(),
        emit=emit,
        health=HealthTracker("tg", ctx.clock),
        clock=ctx.clock,
    )

    def event(text: str, edit_date: datetime | None = None) -> Any:
        message = SimpleNamespace(
            message=text, id=7, date=T0 - timedelta(seconds=2), edit_date=edit_date
        )

        async def get_chat() -> Any:
            return SimpleNamespace(username="squawk")

        return SimpleNamespace(message=message, chat_id=-1001, get_chat=get_chat)

    await collector._handle(event("US CPI MoM 0.4% vs 0.3% exp"), edited=False)  # pyright: ignore[reportPrivateUsage]
    await collector._handle(  # pyright: ignore[reportPrivateUsage]
        event("US CPI MoM 0.4% vs 0.3% exp; prior 0.2%", T0), edited=True
    )
    await collector._handle(event("   "), edited=False)  # pyright: ignore[reportPrivateUsage]
    assert [e.title for e in emitted] == [
        "US CPI MoM 0.4% vs 0.3% exp",
        "US CPI MoM 0.4% vs 0.3% exp; prior 0.2%",
    ]
    assert emitted[0].url_canonical == "https://t.me/squawk/7"
    assert emitted[0].published_at == T0 - timedelta(seconds=2)
    assert emitted[1].published_at == T0
    assert emitted[0].id != emitted[1].id


def test_latency_summary_reports_source_delay_and_stage_latency() -> None:
    from gold_agent.models import LatencyStage
    from gold_agent.ops.metrics import summarize

    stages = [
        LatencyStage(
            ref_id=f"i{n}",
            source_id="fj",
            stage="published",
            ts=T0,
            seconds_from_first_seen=-float(n),
        )
        for n in (2, 4, 6, 8, 100)
    ] + [
        LatencyStage(
            ref_id="i1", source_id="fj", stage="notified", ts=T0, seconds_from_first_seen=1.5
        ),
        LatencyStage(
            ref_id="i1", source_id="fj", stage="first_seen", ts=T0, seconds_from_first_seen=0
        ),
    ]
    summary = {(s.source_id, s.stage): s for s in summarize(stages)}
    published = summary[("fj", "published")]
    assert (
        published.samples == 5 and published.p50_seconds == 6.0 and published.max_seconds == 100.0
    )
    assert summary[("fj", "notified")].p50_seconds == 1.5
    assert ("fj", "first_seen") not in summary

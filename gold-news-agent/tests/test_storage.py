from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from gold_agent.core.app import AppContext
from gold_agent.models import (
    Analysis,
    Candle,
    LatencyStage,
    NewsCluster,
    NewsItem,
    SourceHealth,
    TriageResult,
)
from gold_agent.processing.normalizer import title_hash
from gold_agent.storage.db import UPSERT_CHUNK
from gold_agent.storage.migrate import upgrade
from tests.conftest import T0, postgres_url


def news_item(n: int, **extra: object) -> NewsItem:
    return NewsItem.model_validate(
        {
            "id": f"feed:{n}",
            "source_id": "feed",
            "title": f"Headline {n}",
            "first_seen_at": T0 + timedelta(minutes=n),
            "published_at": T0 + timedelta(minutes=n) - timedelta(seconds=30),
            "raw_hash": title_hash(f"Headline {n}"),
            "tier": 1,
            "reliability": 0.8,
        }
        | extra
    )


def triage(**extra: object) -> TriageResult:
    return TriageResult.model_validate(
        {
            "is_gold_relevant": True,
            "category": "fed_policy",
            "event_type": "breaking",
            "is_new_information": True,
            "direction_gold": "bearish",
            "impact_score": 4,
            "confidence": 0.7,
            "expected_horizon": "hours",
            "priced_in_risk": "low",
            "needs_confirmation": False,
            "entities": ["Fed"],
            "summary_id": "Fed lebih hawkish.",
            "reasoning_id": "Suku bunga lebih tinggi menekan emas.",
            "evidence": ["Fed signals fewer cuts"],
            "source_item_ids": ["feed:1"],
        }
        | extra
    )


def analysis(cluster_id: str, version: int, impact: int = 4) -> Analysis:
    return Analysis(
        id=f"{cluster_id}:{version}",
        cluster_id=cluster_id,
        title="Fed signals fewer cuts",
        source_ids=["feed"],
        item_ids=["feed:1"],
        triage=triage(impact_score=impact),
        origin="llm",
        first_seen_at=T0,
        triaged_at=T0 + timedelta(seconds=version),
        half_life_minutes=120,
        reliability=0.8,
    )


def bar(i: int, *, close: float) -> Candle:
    start = T0 + timedelta(minutes=i)
    return Candle(
        source="mt5",
        tf="M1",
        open_time=start,
        close_time=start + timedelta(minutes=1),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
    )


async def _storage_contract(ctx: AppContext) -> None:
    item = news_item(1)
    assert await ctx.news.save_item(item)
    assert not await ctx.news.save_item(item)  # idempotent archive
    cluster = NewsCluster(
        id=item.id,
        title=item.title,
        lead_item_id=item.id,
        lead_source_id="feed",
        item_ids=[item.id],
        source_ids=["feed"],
        first_seen_at=item.first_seen_at,
        updated_at=item.first_seen_at,
        tier=1,
        reliability=0.8,
    )
    await ctx.news.upsert_cluster(cluster)
    stored = await ctx.news.get_cluster(item.id)
    assert stored is not None and stored.first_seen_at == item.first_seen_at
    assert stored.first_seen_at.tzinfo is not None
    assert len(await ctx.news.recent_clusters(T0)) == 1

    await ctx.news.save_analysis(analysis(item.id, 1, impact=2))
    await ctx.news.save_analysis(analysis(item.id, 2, impact=4))
    latest = await ctx.news.analyses_since(T0 - timedelta(minutes=1))
    assert [a.triage.impact_score for a in latest] == [4]  # latest version per cluster only

    await ctx.ops.record_latency(
        LatencyStage(ref_id=item.id, source_id="feed", stage="first_seen", ts=T0)
    )
    assert len(await ctx.ops.latency_samples(T0 - timedelta(seconds=1))) == 1

    await ctx.ops.upsert_source_health(
        SourceHealth(source_id="feed", status="ok", items_total=3, updated_at=T0)
    )
    await ctx.ops.upsert_source_health(
        SourceHealth(source_id="feed", status="down", consecutive_errors=4, updated_at=T0)
    )
    (health,) = await ctx.ops.source_health()
    assert health.status == "down" and health.consecutive_errors == 4

    await ctx.ops.add_llm_usage(
        ts=T0, route="triage", model="m", cost_usd=0.25, ok=True, latency_ms=900.0
    )
    await ctx.ops.add_llm_usage(ts=T0, route="triage", model="m", cost_usd=0.5, ok=False)
    assert await ctx.ops.llm_spend_since(T0 - timedelta(hours=1)) == pytest.approx(0.75)

    await ctx.ops.kv_set("halt", {"on": True}, T0)
    await ctx.ops.kv_set("halt", {"on": False}, T0)
    assert await ctx.ops.kv_get("halt") == {"on": False}

    await ctx.ops.sync_sources(ctx.sources, T0)
    await ctx.ops.set_learned_reliability("feed", 0.65, 40, T0)
    await ctx.ops.sync_sources(ctx.sources, T0)  # config sync keeps the learned value
    assert await ctx.ops.learned_reliability() == {"feed": 0.65}

    bars = [bar(i, close=100.0 + i) for i in range(UPSERT_CHUNK + 5)]  # spans two statements
    await ctx.market.upsert_candles(bars)
    await ctx.market.upsert_candles([bar(0, close=1.0), bar(0, close=2.0), bar(1, close=3.0)])
    stored = await ctx.market.candles("M1", T0, T0 + timedelta(days=2), source="mt5")
    assert len(stored) == UPSERT_CHUNK + 5  # updated in place, never duplicated
    assert [c.close for c in stored[:3]] == [2.0, 3.0, 102.0]  # a repeated key keeps its last row
    assert stored[-1].close == 100.0 + UPSERT_CHUNK + 4


async def test_storage_contract_sqlite(ctx: AppContext) -> None:
    await _storage_contract(ctx)


async def test_alembic_migration_builds_the_same_schema(
    ctx_factory: Callable[..., AppContext], tmp_path: object
) -> None:
    context = ctx_factory(database_url=f"sqlite+aiosqlite:///{tmp_path}/migrated.db")
    try:
        await upgrade(context.db.url)
        await _storage_contract(context)
    finally:
        await context.close()


async def _schema_diff(context: AppContext) -> list[Any]:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from gold_agent.storage.tables import Base

    def diff(connection: Any) -> list[Any]:
        return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))

    async with context.db.engine.connect() as conn:
        return await conn.run_sync(diff)


async def test_migrations_create_exactly_the_model_schema(
    ctx_factory: Callable[..., AppContext], tmp_path: object
) -> None:
    """A model change without a migration (or the reverse) fails here, not in production."""
    context = ctx_factory(database_url=f"sqlite+aiosqlite:///{tmp_path}/diff.db")
    try:
        await upgrade(context.db.url)
        assert await _schema_diff(context) == []
    finally:
        await context.close()


@pytest.mark.skipif(postgres_url() is None, reason="set GOLD_TEST_POSTGRES_URL for PostgreSQL")
async def test_storage_contract_postgres(ctx_factory: Callable[..., AppContext]) -> None:
    url = postgres_url()
    assert url
    context = ctx_factory(database_url=url)
    try:
        from sqlalchemy import text

        async with context.db.engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await upgrade(context.db.url)
        assert await _schema_diff(context) == []
        await _storage_contract(context)
    finally:
        await context.close()

"""News archive: items, clusters, analyses, fast-path release readings."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import desc, select, update

from gold_agent.models import Analysis, NewsCluster, NewsItem, ReleaseReading
from gold_agent.storage.db import Database
from gold_agent.storage.tables import (
    AnalysisRow,
    AnalysisVersionRow,
    NewsClusterRow,
    NewsItemRow,
    ReleaseRow,
)


class NewsRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_item(self, item: NewsItem) -> bool:
        """Archive every item (relevant or not). Returns False when it was already stored."""
        async with self._db.session() as session:
            return await self._db.insert_ignore(
                session,
                NewsItemRow,
                {
                    "id": item.id,
                    "source_id": item.source_id,
                    "title": item.title,
                    "url": item.url_canonical,
                    "published_at": item.published_at,
                    "first_seen_at": item.first_seen_at,
                    "raw_hash": item.raw_hash,
                    "backfill": item.backfill,
                    "payload": item.model_dump(mode="json"),
                },
                ["id"],
            )

    async def upsert_cluster(self, cluster: NewsCluster) -> None:
        relevant = cluster.prefilter.relevant if cluster.prefilter else None
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                NewsClusterRow,
                {
                    "id": cluster.id,
                    "title": cluster.title,
                    "lead_source_id": cluster.lead_source_id,
                    "first_seen_at": cluster.first_seen_at,
                    "updated_at": cluster.updated_at,
                    "source_count": len(set(cluster.source_ids)),
                    "has_official": cluster.has_official,
                    "relevant": relevant,
                    "backfill": cluster.backfill,
                    "payload": cluster.model_dump(mode="json"),
                },
                ["id"],
            )
            await session.execute(
                update(NewsItemRow)
                .where(NewsItemRow.id.in_(cluster.item_ids))
                .values(cluster_id=cluster.id, relevant=relevant)
            )

    async def recent_clusters(self, since: datetime) -> list[NewsCluster]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(NewsClusterRow)
                .where(NewsClusterRow.updated_at >= since)
                .order_by(NewsClusterRow.first_seen_at)
            )
            return [NewsCluster.model_validate(r.payload) for r in rows]

    async def get_cluster(self, cluster_id: str) -> NewsCluster | None:
        async with self._db.session() as session:
            row = await session.get(NewsClusterRow, cluster_id)
            return NewsCluster.model_validate(row.payload) if row else None

    async def items(self, item_ids: Sequence[str]) -> list[NewsItem]:
        async with self._db.session() as session:
            rows = await session.scalars(select(NewsItemRow).where(NewsItemRow.id.in_(item_ids)))
            return [NewsItem.model_validate(r.payload) for r in rows]

    async def items_between(
        self, start: datetime, end: datetime, *, relevant_only: bool = False
    ) -> list[NewsItem]:
        async with self._db.session() as session:
            query = select(NewsItemRow).where(
                NewsItemRow.first_seen_at >= start, NewsItemRow.first_seen_at < end
            )
            if relevant_only:
                query = query.where(NewsItemRow.relevant.is_(True))
            rows = await session.scalars(query.order_by(NewsItemRow.first_seen_at))
            return [NewsItem.model_validate(r.payload) for r in rows]

    async def latest_clusters(
        self, limit: int = 50, *, relevant_only: bool = True, include_backfill: bool = False
    ) -> list[NewsCluster]:
        async with self._db.session() as session:
            query = select(NewsClusterRow)
            if relevant_only:
                query = query.where(NewsClusterRow.relevant.is_(True))
            if not include_backfill:
                query = query.where(NewsClusterRow.backfill.is_(False))
            rows = await session.scalars(
                query.order_by(desc(NewsClusterRow.first_seen_at)).limit(limit)
            )
            return [NewsCluster.model_validate(r.payload) for r in rows]

    async def save_analysis(self, analysis: Analysis) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                AnalysisRow,
                {
                    "id": analysis.id,
                    "cluster_id": analysis.cluster_id,
                    "origin": analysis.origin,
                    "model": analysis.model,
                    "category": analysis.triage.category,
                    "direction": analysis.triage.direction_gold,
                    "impact": analysis.triage.impact_score,
                    "confidence": analysis.triage.confidence,
                    "relevant": analysis.triage.is_gold_relevant,
                    "first_seen_at": analysis.first_seen_at,
                    "triaged_at": analysis.triaged_at,
                    "analyzed_at": analysis.analyzed_at,
                    "payload": analysis.model_dump(mode="json"),
                },
                ["id"],
            )
            session.add(
                AnalysisVersionRow(
                    cluster_id=analysis.cluster_id,
                    version_at=analysis.version_at or analysis.triaged_at,
                    payload=analysis.model_dump(mode="json"),
                )
            )

    async def analysis_versions_between(self, start: datetime, end: datetime) -> list[Analysis]:
        """Every version published in [start, end), oldest first (for no-lookahead replays)."""
        async with self._db.session() as session:
            rows = await session.scalars(
                select(AnalysisVersionRow)
                .where(AnalysisVersionRow.version_at >= start, AnalysisVersionRow.version_at < end)
                .order_by(AnalysisVersionRow.version_at, AnalysisVersionRow.id)
            )
            versions: list[Analysis] = []
            for row in rows:
                analysis = Analysis.model_validate(row.payload)
                versions.append(analysis.model_copy(update={"version_at": row.version_at}))
            return versions

    async def get_analysis(self, cluster_id: str) -> Analysis | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(AnalysisRow)
                .where(AnalysisRow.cluster_id == cluster_id)
                .order_by(desc(AnalysisRow.triaged_at))
                .limit(1)
            )
            return Analysis.model_validate(row.payload) if row else None

    async def analyses_since(
        self, since: datetime, *, relevant_only: bool = True
    ) -> list[Analysis]:
        """Latest analysis per cluster first seen at/after `since` (first_seen_at: no lookahead)."""
        async with self._db.session() as session:
            query = select(AnalysisRow).where(AnalysisRow.first_seen_at >= since)
            if relevant_only:
                query = query.where(AnalysisRow.relevant.is_(True))
            rows = await session.scalars(query.order_by(AnalysisRow.triaged_at))
            latest: dict[str, Analysis] = {}
            for row in rows:
                latest[row.cluster_id] = Analysis.model_validate(row.payload)
            return sorted(latest.values(), key=lambda a: a.first_seen_at)

    async def analyses_between(self, start: datetime, end: datetime) -> list[Analysis]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(AnalysisRow)
                .where(AnalysisRow.first_seen_at >= start, AnalysisRow.first_seen_at < end)
                .order_by(AnalysisRow.first_seen_at, AnalysisRow.triaged_at)
            )
            latest: dict[str, Analysis] = {}
            for row in rows:
                latest[row.cluster_id] = Analysis.model_validate(row.payload)
            return sorted(latest.values(), key=lambda a: a.first_seen_at)

    async def latest_analyses(self, limit: int = 50) -> list[Analysis]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(AnalysisRow).order_by(desc(AnalysisRow.triaged_at)).limit(limit)
            )
            return [Analysis.model_validate(r.payload) for r in rows]

    async def save_release(self, reading: ReleaseReading) -> None:
        async with self._db.session() as session:
            session.add(
                ReleaseRow(
                    event_key=reading.event_key,
                    cluster_id=reading.cluster_id,
                    actual=reading.actual,
                    forecast=reading.forecast,
                    z=reading.z,
                    bucket=reading.bucket,
                    first_seen_at=reading.first_seen_at,
                    payload=reading.model_dump(mode="json"),
                )
            )

    async def releases_before(self, before: datetime, limit: int) -> list[ReleaseReading]:
        """The newest `limit` releases first seen before `before`, newest first."""
        async with self._db.session() as session:
            rows = await session.scalars(
                select(ReleaseRow)
                .where(ReleaseRow.first_seen_at < before)
                .order_by(desc(ReleaseRow.first_seen_at))
                .limit(limit)
            )
            return [ReleaseReading.model_validate(r.payload) for r in rows]

    async def releases_for(self, event_key: str, limit: int = 100) -> list[ReleaseReading]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(ReleaseRow)
                .where(ReleaseRow.event_key == event_key)
                .order_by(desc(ReleaseRow.first_seen_at))
                .limit(limit)
            )
            return [ReleaseReading.model_validate(r.payload) for r in rows]

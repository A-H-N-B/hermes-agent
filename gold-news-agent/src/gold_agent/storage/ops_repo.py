"""Operational state: sources, health, latency, LLM usage, alerts, heartbeats, key-value flags."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, desc, func, select, update

from gold_agent.config import SourceRegistry
from gold_agent.models import Alert, LatencyStage, SourceHealth
from gold_agent.storage.db import Database
from gold_agent.storage.tables import (
    AlertRow,
    HeadlineLabelRow,
    HeartbeatRow,
    KvStateRow,
    LatencyRow,
    LlmUsageRow,
    SourceHealthRow,
    SourceRow,
)


class OpsRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ sources

    async def sync_sources(self, registry: SourceRegistry, now: datetime) -> None:
        """Mirror sources.yaml into the DB, keeping learned reliability across restarts."""
        async with self._db.session() as session:
            for source in registry.sources:
                await self._db.upsert(
                    session,
                    SourceRow,
                    {
                        "id": source.id,
                        "name": source.name,
                        "kind": source.kind.value,
                        "type": source.type.value,
                        "tier": source.tier,
                        "reliability": source.reliability,
                        "reliability_samples": 0,
                        "enabled": source.enabled,
                        "status": source.status.value,
                        "updated_at": now,
                    },
                    ["id"],
                    update=[
                        "name",
                        "kind",
                        "type",
                        "tier",
                        "reliability",
                        "enabled",
                        "status",
                        "updated_at",
                    ],
                )

    async def learned_reliability(self) -> dict[str, float]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SourceRow).where(SourceRow.reliability_learned.is_not(None))
            )
            return {r.id: float(r.reliability_learned or 0.0) for r in rows}

    async def set_learned_reliability(
        self, source_id: str, value: float, samples: int, now: datetime
    ) -> None:
        async with self._db.session() as session:
            row = await session.get(SourceRow, source_id)
            if row is not None:
                row.reliability_learned = value
                row.reliability_samples = samples
                row.updated_at = now

    # ------------------------------------------------------------------ health + latency

    async def upsert_source_health(self, health: SourceHealth) -> None:
        async with self._db.session() as session:
            await self._db.upsert(session, SourceHealthRow, health.model_dump(), ["source_id"])

    async def source_health(self) -> list[SourceHealth]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SourceHealthRow).order_by(SourceHealthRow.source_id)
            )
            return [
                SourceHealth.model_validate(
                    {c.name: getattr(r, c.name) for c in SourceHealthRow.__table__.columns}
                )
                for r in rows
            ]

    async def record_latency(self, stage: LatencyStage) -> None:
        async with self._db.session() as session:
            session.add(LatencyRow(**stage.model_dump()))

    async def latency_samples(self, since: datetime) -> list[LatencyStage]:
        async with self._db.session() as session:
            rows = await session.scalars(select(LatencyRow).where(LatencyRow.ts >= since))
            return [
                LatencyStage(
                    ref_id=r.ref_id,
                    source_id=r.source_id,
                    stage=r.stage,  # type: ignore[arg-type]
                    ts=r.ts,
                    seconds_from_first_seen=r.seconds_from_first_seen,
                )
                for r in rows
            ]

    # ------------------------------------------------------------------ LLM usage

    async def add_llm_usage(self, **values: Any) -> int:
        async with self._db.session() as session:
            row = LlmUsageRow(**values)
            session.add(row)
            await session.flush()
            return row.id

    async def update_llm_usage(self, row_id: int, **values: Any) -> None:
        async with self._db.session() as session:
            await session.execute(
                update(LlmUsageRow).where(LlmUsageRow.id == row_id).values(**values)
            )

    async def delete_llm_usage(self, row_id: int) -> None:
        async with self._db.session() as session:
            await session.execute(delete(LlmUsageRow).where(LlmUsageRow.id == row_id))

    async def llm_spend_since(self, since: datetime) -> float:
        async with self._db.session() as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(LlmUsageRow.cost_usd), 0.0)).where(
                    LlmUsageRow.ts >= since
                )
            )
            return float(total or 0.0)

    async def llm_usage_summary(self, since: datetime) -> list[dict[str, Any]]:
        async with self._db.session() as session:
            rows = await session.execute(
                select(
                    LlmUsageRow.route,
                    LlmUsageRow.model,
                    func.count(),
                    func.sum(LlmUsageRow.cost_usd),
                    func.avg(LlmUsageRow.latency_ms),
                    func.sum(case((LlmUsageRow.ok.is_(True), 1), else_=0)),
                )
                .where(LlmUsageRow.ts >= since)
                .group_by(LlmUsageRow.route, LlmUsageRow.model)
            )
            return [
                {
                    "route": route,
                    "model": model,
                    "calls": int(calls),
                    "cost_usd": float(cost or 0.0),
                    "avg_latency_ms": float(latency or 0.0),
                    "ok": int(ok or 0),
                }
                for route, model, calls, cost, latency, ok in rows
            ]

    async def llm_latencies(self, route: str, since: datetime) -> list[float]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(LlmUsageRow.latency_ms).where(
                    LlmUsageRow.route == route, LlmUsageRow.ts >= since, LlmUsageRow.ok.is_(True)
                )
            )
            return [float(v) for v in rows]

    # ------------------------------------------------------------------ alerts

    async def save_alert(self, alert: Alert) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                AlertRow,
                {
                    "id": alert.id,
                    "kind": alert.kind,
                    "severity": alert.severity,
                    "created_at": alert.created_at,
                    "dedupe_key": alert.dedupe_key,
                    "payload": alert.model_dump(mode="json"),
                },
                ["id"],
            )

    async def recent_alerts(self, limit: int = 50) -> list[Alert]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(AlertRow).order_by(desc(AlertRow.created_at)).limit(limit)
            )
            return [Alert.model_validate(r.payload) for r in rows]

    # ------------------------------------------------------------------ heartbeats + kv

    async def heartbeat(self, name: str, ts: datetime, info: dict[str, Any] | None = None) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session, HeartbeatRow, {"name": name, "ts": ts, "info": info or {}}, ["name"]
            )

    async def heartbeats(self) -> dict[str, datetime]:
        async with self._db.session() as session:
            rows = await session.scalars(select(HeartbeatRow))
            return {r.name: r.ts for r in rows}

    async def kv_get(self, key: str) -> dict[str, Any] | None:
        async with self._db.session() as session:
            row = await session.get(KvStateRow, key)
            return dict(row.value) if row else None

    async def kv_set(self, key: str, value: dict[str, Any], now: datetime) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session, KvStateRow, {"key": key, "value": value, "updated_at": now}, ["key"]
            )

    # ------------------------------------------------------------------ labelled headlines

    async def save_label(
        self, item_id: str, labeler: str, label: dict[str, Any], now: datetime
    ) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                HeadlineLabelRow,
                {"item_id": item_id, "labeler": labeler, "created_at": now, "label": label},
                ["item_id", "labeler"],
            )

    async def labels(self) -> list[tuple[str, str, dict[str, Any]]]:
        async with self._db.session() as session:
            rows = await session.scalars(select(HeadlineLabelRow))
            return [(r.item_id, r.labeler, dict(r.label)) for r in rows]

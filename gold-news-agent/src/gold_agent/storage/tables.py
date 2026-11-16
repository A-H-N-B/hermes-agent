"""Table definitions (section 14). Scalar columns are for querying; `payload` keeps the model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Always aware UTC in Python; naive UTC on SQLite, timestamptz on PostgreSQL."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given to UTCDateTime")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


JsonType = JSON().with_variant(JSONB(), "postgresql")
BigIntId = BigInteger().with_variant(Integer(), "sqlite")  # SQLite autoincrement needs INTEGER


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JsonType, list[Any]: JsonType}


class SourceRow(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(16))
    tier: Mapped[int] = mapped_column(Integer)
    reliability: Mapped[float] = mapped_column(Float)
    reliability_learned: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_samples: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class NewsItemRow(Base):
    __tablename__ = "news_items"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    raw_hash: Mapped[str] = mapped_column(String(64), index=True)
    cluster_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    relevant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    backfill: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column()


class NewsClusterRow(Base):
    __tablename__ = "news_clusters"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    lead_source_id: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    source_count: Mapped[int] = mapped_column(Integer)
    has_official: Mapped[bool] = mapped_column(Boolean, default=False)
    relevant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    backfill: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column()


class AnalysisRow(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(String(96), index=True)
    origin: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))
    impact: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    relevant: Mapped[bool] = mapped_column(Boolean)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    triaged_at: Mapped[datetime] = mapped_column(UTCDateTime())
    analyzed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column()


class AnalysisVersionRow(Base):
    """Append-only: every published version with the time it became known, so a replay never
    sees a later confirmation or deep analysis before it existed."""

    __tablename__ = "analysis_versions"
    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(String(96), index=True)
    version_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column()


class ReleaseRow(Base):
    __tablename__ = "releases"
    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(64), index=True)
    cluster_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    actual: Mapped[float] = mapped_column(Float)
    forecast: Mapped[float | None] = mapped_column(Float, nullable=True)
    z: Mapped[float | None] = mapped_column(Float, nullable=True)
    bucket: Mapped[str] = mapped_column(String(24))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column()


class CalendarEventRow(Base):
    __tablename__ = "calendar_events"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(8), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    impact: Mapped[str] = mapped_column(String(16))
    event_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    forecast: Mapped[str | None] = mapped_column(String(32), nullable=True)
    previous: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actual: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict[str, Any]] = mapped_column()


class PlaybookRow(Base):
    __tablename__ = "playbooks"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(96), index=True)
    event_key: Mapped[str] = mapped_column(String(64))
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column()


class TickRow(Base):
    __tablename__ = "ticks"
    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime())
    source: Mapped[str] = mapped_column(String(32))
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    __table_args__ = (Index("ix_ticks_source_ts", "source", "ts"),)


class CandleRow(Base):
    __tablename__ = "candles"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    tf: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    close_time: Mapped[datetime] = mapped_column(UTCDateTime())
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    ticks: Mapped[int] = mapped_column(Integer, default=0)


class MarketContextRow(Base):
    __tablename__ = "market_context"
    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column()


class SignalRow(Base):
    __tablename__ = "signals"
    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    action: Mapped[str] = mapped_column(String(8))
    setup_type: Mapped[str] = mapped_column(String(24))
    confidence: Mapped[int] = mapped_column(Integer)
    composite_score: Mapped[float] = mapped_column(Float)
    entry_low: Mapped[float] = mapped_column(Float)
    entry_high: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    tp1: Mapped[float] = mapped_column(Float)
    tp2: Mapped[float] = mapped_column(Float)
    tp3: Mapped[float] = mapped_column(Float)
    valid_until: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict[str, Any]] = mapped_column()


class SignalStateRow(Base):
    __tablename__ = "signal_states"
    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    action: Mapped[str] = mapped_column(String(8))
    composite_score: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column()


class SignalOutcomeRow(Base):
    __tablename__ = "signal_outcomes"
    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column()


class HourlyProfileRow(Base):
    __tablename__ = "hourly_profile"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    basis: Mapped[str] = mapped_column(String(24))
    bucket: Mapped[int] = mapped_column(Integer)
    samples: Mapped[int] = mapped_column(Integer)
    mean_range: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_range: Mapped[float | None] = mapped_column(Float, nullable=True)
    std_range: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_up: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime())


class OutlookRowTable(Base):
    __tablename__ = "outlooks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    trigger: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column()


class NotificationRow(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    priority: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    suppressed_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text)


class LatencyRow(Base):
    __tablename__ = "latency_metrics"
    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    ref_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    seconds_from_first_seen: Mapped[float | None] = mapped_column(Float, nullable=True)


class LlmUsageRow(Base):
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    route: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SourceHealthRow(Base):
    __tablename__ = "source_health"
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_item_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    items_total: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AlertRow(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column()


class KvStateRow(Base):
    __tablename__ = "kv_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class HeartbeatRow(Base):
    __tablename__ = "service_heartbeats"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime())
    info: Mapped[dict[str, Any]] = mapped_column()


class HeadlineLabelRow(Base):
    __tablename__ = "headline_labels"
    item_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    labeler: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    label: Mapped[dict[str, Any]] = mapped_column()

"""Operational records: source health, latency stages, alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LatencyStageName = Literal[
    "published", "first_seen", "triaged", "analyzed", "signal", "notified", "flash"
]


class LatencyStage(BaseModel):
    ref_id: str  # news item / cluster id
    source_id: str
    stage: LatencyStageName
    ts: datetime
    seconds_from_first_seen: float | None = None


class SourceHealth(BaseModel):
    source_id: str
    status: Literal["ok", "degraded", "down", "idle"]
    last_success_at: datetime | None = None
    last_item_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    consecutive_errors: int = 0
    items_total: int = 0
    updated_at: datetime


class Alert(BaseModel):
    id: str
    kind: Literal["volatility", "spread", "health", "budget", "clock", "stale_price", "recovery"]
    severity: Literal["info", "warning", "critical"]
    title: str
    detail: str
    created_at: datetime
    dedupe_key: str
    ref_ids: list[str] = []

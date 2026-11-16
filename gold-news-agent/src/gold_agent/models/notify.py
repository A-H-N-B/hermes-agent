"""Outgoing notifications (Telegram)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from gold_agent.models.common import Priority

NotificationKind = Literal[
    "signal",
    "signal_update",
    "flash",
    "outlook",
    "playbook",
    "volatility",
    "brief",
    "daily_report",
    "weekly_report",
    "health",
    "digest",
    "news",
]


class Notification(BaseModel):
    id: str
    kind: NotificationKind
    priority: Priority
    text: str
    created_at: datetime
    dedupe_key: str | None = None
    ref_ids: list[str] = Field(default_factory=list)
    first_seen_at: datetime | None = None  # source item time, for notified latency
    sent_at: datetime | None = None
    suppressed_reason: str | None = None

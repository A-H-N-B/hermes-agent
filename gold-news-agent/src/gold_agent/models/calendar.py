"""Economic calendar events and pre-computed event playbooks (section 11.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from gold_agent.models.common import Direction


class CalendarEvent(BaseModel):
    id: str
    title: str
    country: str
    scheduled_at: datetime
    impact: Literal["high", "medium", "low", "holiday", "unknown"]
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None
    event_key: str | None = None  # matched spec in config/events.yaml
    source_id: str
    all_day: bool = False


class PlaybookBucket(BaseModel):
    name: Literal["strong_bearish", "mild_bearish", "neutral", "mild_bullish", "strong_bullish"]
    hawkish_z_min: float | None
    hawkish_z_max: float | None
    actual_min: float | None = None
    actual_max: float | None = None
    direction_gold: Direction
    hint_id: str  # what to do, in Bahasa Indonesia


class Playbook(BaseModel):
    id: str
    event_id: str
    event_key: str
    event_title: str
    scheduled_at: datetime
    forecast: float | None
    previous: float | None
    unit: str
    surprise_std: float
    z_basis: Literal["calibrated", "prior"]
    buckets: list[PlaybookBucket]
    created_at: datetime
    sent_at: datetime | None = None
    notes: list[str] = Field(default_factory=list)

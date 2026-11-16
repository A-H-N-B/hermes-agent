"""Analysis payloads: LLM triage (section 13.2), deep analysis, and fast-path release readings."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from gold_agent.models.common import Category, Direction, EventType


class SurpriseInfo(BaseModel):
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    unit: str | None = None
    surprise_value: float | None = None


class TriageResult(BaseModel):
    """Schema the triage model must fill. Ranges are enforced here, not trusted from the model."""

    is_gold_relevant: bool
    category: Category
    event_type: EventType
    is_new_information: bool
    direction_gold: Direction
    impact_score: int
    confidence: float
    expected_horizon: Literal["minutes", "hours", "days", "weeks"]
    surprise: SurpriseInfo | None = None
    priced_in_risk: Literal["low", "medium", "high"]
    needs_confirmation: bool
    entities: list[str]
    summary_id: str
    reasoning_id: str
    evidence: list[str]
    source_item_ids: list[str]

    @field_validator("impact_score")
    @classmethod
    def _impact_range(cls, value: int) -> int:
        if not 0 <= value <= 5:
            raise ValueError("impact_score must be 0..5")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be 0..1")
        return value


class DeepAnalysis(BaseModel):
    """Section 7.7: narrative, follow-up scenarios and risks for high-impact stories."""

    narrative_id: str
    scenarios: list[str]
    risks: list[str]
    key_levels_note_id: str
    direction_gold: Direction
    confidence: float
    evidence: list[str]

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be 0..1")
        return value


class ReleaseReading(BaseModel):
    """Fast path (7.5): a data-release headline parsed into numbers without any LLM."""

    event_key: str
    event_name: str
    headline: str
    cluster_id: str | None = None
    source_id: str | None = None
    actual: float
    forecast: float | None = None
    previous: float | None = None
    previous_revised: float | None = None
    forecast_source: Literal["headline", "calendar", "none"] = "headline"
    unit: str
    surprise: float | None = None
    z: float | None = None
    hawkish_z: float | None = None
    z_basis: Literal["calibrated", "prior", "none"] = "none"
    bucket: Literal[
        "strong_bearish", "mild_bearish", "neutral", "mild_bullish", "strong_bullish", "unknown"
    ] = "unknown"
    direction_gold: Direction = "neutral"
    first_seen_at: datetime
    parsed_at: datetime


class Analysis(BaseModel):
    """One analysis of one cluster. `origin` says who produced the direction/impact."""

    id: str
    cluster_id: str
    title: str
    source_ids: list[str]
    item_ids: list[str]
    triage: TriageResult
    origin: Literal["llm", "rules", "fast_path"]
    model: str | None = None
    prompt_version: str | None = None
    ai_pending: bool = False  # rules fallback while the LLM was down/over budget/invalid
    deep: DeepAnalysis | None = None
    deep_model: str | None = None
    first_seen_at: datetime
    published_at: datetime | None = None
    triaged_at: datetime
    analyzed_at: datetime | None = None
    half_life_minutes: float
    reliability: float
    confirmed: bool = False
    weight: float = (
        1.0  # verification multiplier (needs_confirmation -> settings.news.unconfirmed_weight)
    )
    release: ReleaseReading | None = None
    notes: list[str] = Field(default_factory=list)
    version_at: datetime | None = None  # when this version was published (replay visibility)

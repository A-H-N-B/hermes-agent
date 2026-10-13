"""News items (one per source item) and clusters (one per story)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field


class NewsItem(BaseModel):
    """Section 7.2. `first_seen_at` is when *we* received it — the only honest backtest clock."""

    model_config = ConfigDict(frozen=True)

    id: str
    source_id: str
    url_canonical: str | None = None
    title: str
    body: str | None = None
    lang: str = "en"
    published_at: datetime | None = None
    first_seen_at: datetime
    raw_hash: str
    tier: int
    reliability: float
    official: bool = False
    backfill: bool = False  # seen on a collector's first poll: archive only, never a trigger


class PrefilterResult(BaseModel):
    relevant: bool
    categories: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    elapsed_ms: float = 0.0


class NewsCluster(BaseModel):
    """Similar items merged into one story. The first source fixes the story's time."""

    id: str
    title: str
    lead_item_id: str
    lead_source_id: str
    item_ids: list[str]
    source_ids: list[str]
    first_seen_at: datetime
    published_at: datetime | None = None
    updated_at: datetime
    tier: int
    reliability: float
    has_official: bool = False
    backfill: bool = False
    is_new: bool = True
    prefilter: PrefilterResult | None = None
    body: str | None = None

    @computed_field
    @property
    def confirmation_count(self) -> int:
        return max(0, len(set(self.source_ids)) - 1)

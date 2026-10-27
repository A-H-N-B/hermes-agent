"""Daily fundamentals of gold (fundamentals/): each driver, its reading and its pull on gold."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Factor(BaseModel):
    key: str
    label: str  # Indonesian
    value: str  # the reading as people say it ("1.92%", "+212 rb kontrak")
    change: str | None = None  # "−6 bp dalam 5 hari"
    effect: float  # pull on gold, -1 (bearish) .. +1 (bullish)
    weight: float  # share of the daily bias
    note: str  # one line: what it means for gold now
    as_of: str | None = None  # date of the data
    source: str


class FundamentalSnapshot(BaseModel):
    built_at: datetime
    factors: list[Factor] = Field(default_factory=lambda: list[Factor]())
    bias: float | None = None  # weighted pull, -1 .. +1; None without any factor
    summary: str
    missing: list[str] = Field(default_factory=list)  # drivers without data, by label

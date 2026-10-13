"""Shared literals and helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
Direction = Literal["bullish", "bearish", "neutral", "mixed"]
Action = Literal["BUY", "SELL", "WAIT"]
Priority = Literal["high", "normal", "digest"]
Category = Literal[
    "fed_policy",
    "us_inflation",
    "us_labor",
    "us_growth",
    "usd",
    "yields",
    "geopolitics",
    "central_bank_gold",
    "trade_tariff",
    "fiscal_debt",
    "risk_sentiment",
    "physical_demand",
    "etf_flows",
    "commentary",
    "other",
]
EventType = Literal["scheduled_release", "speech", "breaking", "analysis", "rumor", "recap"]

DIRECTION_SIGN: dict[str, int] = {"bullish": 1, "bearish": -1, "neutral": 0, "mixed": 0}


def utc_now() -> datetime:
    return datetime.now(UTC)

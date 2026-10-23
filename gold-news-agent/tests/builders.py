"""Small builders for signal/outlook tests (realistic shapes, explicit numbers)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from gold_agent.models import Analysis, CalendarEvent, Level, MarketContext, TimeframeState
from gold_agent.models.analysis import TriageResult
from gold_agent.signals.scoring import PriceAt

T0 = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)  # Tuesday 19:30 WIB, London-NY overlap

Trend = Literal["up", "down", "flat"]


def triage(**extra: Any) -> TriageResult:
    return TriageResult.model_validate(
        {
            "is_gold_relevant": True,
            "category": "geopolitics",
            "event_type": "breaking",
            "is_new_information": True,
            "direction_gold": "bullish",
            "impact_score": 5,
            "confidence": 0.9,
            "expected_horizon": "hours",
            "priced_in_risk": "low",
            "needs_confirmation": False,
            "entities": [],
            "summary_id": "Serangan rudal memicu permintaan aset aman.",
            "reasoning_id": "Risiko geopolitik naik, emas diburu.",
            "evidence": ["missile strike"],
            "source_item_ids": ["wire:1"],
        }
        | extra
    )


def analysis(
    cid: str = "c1",
    *,
    first_seen: datetime = T0,
    half_life: float = 120,
    reliability: float = 0.9,
    weight: float = 1.0,
    sources: Sequence[str] = ("wire",),
    **triage_extra: Any,
) -> Analysis:
    return Analysis(
        id=f"{cid}:1",
        cluster_id=cid,
        title=f"Headline {cid}",
        source_ids=list(sources),
        item_ids=[f"{sources[0]}:{cid}"],
        triage=triage(**triage_extra),
        origin="llm",
        model="claude-haiku-5-5",
        prompt_version="abc123",
        first_seen_at=first_seen,
        triaged_at=first_seen + timedelta(seconds=2),
        half_life_minutes=half_life,
        reliability=reliability,
        weight=weight,
    )


def market(
    mid: float = 2650.0,
    *,
    ts: datetime = T0,
    spread: float = 0.2,
    atr_m15: float | None = 4.0,
    atr_h1: float | None = 8.0,
    atr_d1: float | None = 30.0,
    trend_h1: Trend = "up",
    trend_h4: Trend = "up",
    rsi_h1: float = 60.0,
    vwap: float | None = None,
    levels: Sequence[Level] = (),
    market_open: bool = True,
    session: str = "overlap",
    liquidity: float = 1.0,
    **extra: Any,
) -> MarketContext:
    # The intraday charts a trade is timed on: EMA20 sits on the trend's side of the price.
    lean = {"up": -1.0, "down": 1.0, "flat": 0.0}[trend_h1]
    intraday = {
        "M5": TimeframeState(tf="M5", atr14=2.0, ema20=mid + lean * 0.5, trend=trend_h1, bars=200),
        "M15": TimeframeState(tf="M15", atr14=atr_m15, ema20=mid + lean, trend="up", bars=200),
    }
    return MarketContext.model_validate(
        {
            "ts": ts,
            "source": "mt5",
            "proxy": False,
            "bid": mid - spread / 2,
            "ask": mid + spread / 2,
            "mid": mid,
            "spread": spread,
            "tick_age_seconds": 1.0,
            "stale": False,
            "market_open": market_open,
            "session": session,
            "liquidity_factor": liquidity,
            "timeframes": {
                **intraday,
                "H1": TimeframeState(tf="H1", atr14=atr_h1, trend=trend_h1, rsi14=rsi_h1, bars=200),
                "H4": TimeframeState(tf="H4", trend=trend_h4, bars=200),
                "D1": TimeframeState(tf="D1", atr14=atr_d1, bars=200),
            },
            "vwap": vwap,
            "levels": list(levels),
        }
        | extra
    )


def event(
    title: str = "CPI m/m",
    *,
    at: datetime = T0,
    impact: Literal["high", "medium", "low"] = "high",
    key: str | None = "us_cpi_mom",
    forecast: str | None = "0.3%",
    eid: str | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        id=eid or f"ev-{title}-{at:%Y%m%d%H%M}",
        title=title,
        country="USD",
        scheduled_at=at,
        impact=impact,
        forecast=forecast,
        previous="0.2%",
        event_key=key,
        source_id="forexfactory",
    )


def flat_prices(price: float | None) -> PriceAt:
    """price_at stub: the market was at `price` for the whole lookback."""

    def at(_moment: datetime) -> float | None:
        return price

    return at

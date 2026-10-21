"""Signals (schema 10.5), live engine state, outcomes and the 24h hourly outlook (11.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from gold_agent.models.common import Action

SetupType = Literal["news_momentum", "news_fade", "trend_continuation", "breakout", "range"]
EntryType = Literal["market", "limit", "stop"]


class SignalComponents(BaseModel):
    N: float
    M: float
    T: float
    P: float
    session_factor: float


class SignalReview(BaseModel):
    """Opus 5.5's final say on a signal (signals/desk.py). `model` None: the AI could not review
    it (outage, budget, daily cap) and the engine decided alone; `note` says why."""

    model: str | None
    confidence: int | None = None
    timeframe: str | None = None  # the chart the AI chose
    reasons: list[str] = Field(default_factory=list)
    caution: str | None = None
    note: str | None = None


class Signal(BaseModel):
    signal_id: str
    created_at_utc: datetime
    created_at_wib: str
    session: str
    action: Action
    setup_type: SetupType
    entry_type: EntryType
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    tp3_source: str | None = None  # structure level name, or "3R"
    risk_reward: float
    risk_pct: float
    lot_size: float | None
    lot_note: str | None = None
    valid_until_utc: datetime
    valid_until_wib: str
    confidence: int
    composite_score: float
    components: SignalComponents
    top_reasons: list[str]
    invalidation: list[str]
    upcoming_events_4h: list[str]
    linked_news_ids: list[str]
    model_versions: dict[str, str]
    config_hash: str
    price_source: str
    proxy_price: bool
    bid: float
    ask: float
    spread: float
    sl_distance: float
    sl_atr_multiple: float
    first_seen_at: datetime | None = None  # earliest linked news, for end-to-end latency
    trigger_summary: str | None = None
    trigger_source: str | None = None
    trigger_category: str | None = None  # news category of the trigger (per-category stats)
    timeframe: str | None = None  # chart the levels, validity and entry timing were sized on
    timeframe_reason: str | None = None  # why that chart (signals/timeframe.py, or Opus)
    review: SignalReview | None = None  # Opus 5.5's final decision; None = engine alone


class SignalState(BaseModel):
    """What the engine thinks right now; WAIT carries its reasons."""

    timeframe: str | None = None  # the chart chosen for trading right now
    timeframe_reason: str | None = None

    ts: datetime
    action: Action
    composite_score: float
    confidence: int
    components: SignalComponents
    reasons: list[str] = Field(default_factory=list)
    wait_reasons: list[str] = Field(default_factory=list)
    active_signal_id: str | None = None
    halted: bool = False


OutcomeStatus = Literal["open", "tp1", "tp2", "tp3", "sl", "breakeven", "expired", "cancelled"]


class SignalOutcome(BaseModel):
    """Paper-trading result of one signal (R = multiples of the initial risk per unit)."""

    signal_id: str
    action: Literal["BUY", "SELL"] = "BUY"
    status: OutcomeStatus
    entry_price: float  # actual paper fill (ask/bid + slippage), not the signal's entry zone
    exit_price: float | None = None  # last exit
    opened_at: datetime
    closed_at: datetime | None = None
    r_multiple: float | None = None
    mfe_r: float = 0.0
    mae_r: float = 0.0
    tp1_hit_at: datetime | None = None
    duration_minutes: float | None = None
    risk_per_unit: float | None = None  # |fill - initial stop|
    stop: float | None = None  # current stop (breakeven after TP1)
    remaining: float = 1.0  # fraction of the position still open
    realized_r: float = 0.0  # R already booked by partial exits
    last_price_at: datetime | None = None  # last quote applied (restart catch-up point)
    price_source: str | None = None
    notes: list[str] = Field(default_factory=list)


class OutlookRow(BaseModel):
    hour_start_utc: datetime
    label_wib: str  # "19:00–20:00"
    session: str
    events: list[str]
    expected_range_usd: float | None
    volatility_label: str
    bias: str
    scenario: str
    confidence: int | None
    risky: list[str]
    conditional: bool


class Outlook(BaseModel):
    id: str
    created_at: datetime
    trigger: str
    rows: list[OutlookRow]
    notes: list[str] = Field(default_factory=list)
    narrative_id: str | None = None
    model: str | None = None

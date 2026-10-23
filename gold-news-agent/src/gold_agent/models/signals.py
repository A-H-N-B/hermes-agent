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
    """The AI committee's final say on a signal (signals/desk.py). `model` (the lead answer's)
    None: the AI could not review it (outage, budget, daily cap) and the engine decided alone;
    `note` says why."""

    model: str | None
    models: list[str] = Field(default_factory=list)  # every model that answered
    confidence: int | None = None
    timeframe: str | None = None  # the chart the AI chose
    reasons: list[str] = Field(default_factory=list)
    caution: str | None = None
    note: str | None = None
    votes: int | None = None  # independent committee answers counted
    agree: int | None = None  # how many of them back this decision
    dissent: str | None = None  # the strongest opposing answer's main reason


class TradeOdds(BaseModel):
    """Chances of the trade plan within its validity (signals/odds.py): the volatility model
    blended with how past setups like it ended. Probabilities, not promises."""

    tp1: float  # TP1 reached before the stop
    stop: float  # the stop first
    neither: float  # neither before the signal expires
    tp1_ci95: tuple[float, float] | None = None
    tp2_after_tp1: float  # the runner reaching TP2 before breakeven, once TP1 is hit
    ev_r: float  # expected result per trade in R (after the spread)
    ev_r_ci95: tuple[float, float] | None = None
    model_tp1: float  # the volatility model alone
    setups: int = 0  # past setups behind the blend
    setups_tp1: int = 0
    setups_stop: int = 0
    setups_neither: int = 0
    drift_basis: str  # what the model assumes about direction, in words
    sigma: float  # volatility per bar used ($)
    sigma_model: str  # garch | ewma | atr
    horizon_bars: int
    cost_r: float  # the spread as a share of the risk
    tail_nu: float | None = None  # Student-t bars in the simulation; None = normal
    revert_half_life: float | None = None  # Ornstein-Uhlenbeck pull in the simulation (bars)
    paths: int = 0  # simulated price paths
    model_note: str = ""  # how the model was set up, in words
    meta_tp1: float | None = None  # the meta-labeling model's P(TP1 first) for this chart state
    meta_note: str | None = None  # what it is based on and whether it counts
    note: str  # one line for people


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
    timeframe_reason: str | None = None  # why that chart (signals/timeframe.py, or the AI)
    review: SignalReview | None = None  # the AI committee's final say; None = engine alone
    odds: TradeOdds | None = None  # chances of TP1 / stop and the expected value
    physics_note: str | None = None  # the chart's and the market's econophysics, one line


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

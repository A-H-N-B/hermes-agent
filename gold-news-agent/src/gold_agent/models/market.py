"""Prices, candles and the market context snapshot handed to the signal engine."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from gold_agent.models.common import Timeframe


class Tick(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts: datetime  # exchange/broker event time when available, else receive time
    bid: float
    ask: float
    last: float | None = None
    volume: float = 0.0
    source: str
    proxy: bool = False  # True for PAXG and other non-XAUUSD stand-ins
    received_at: datetime

    @computed_field
    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @computed_field
    @property
    def spread(self) -> float:
        return self.ask - self.bid


class Candle(BaseModel):
    source: str
    tf: Timeframe
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    ticks: int = 0
    complete: bool = True


class Level(BaseModel):
    name: str
    price: float
    kind: Literal["support", "resistance", "pivot", "round", "range"]


class TimeframeState(BaseModel):
    tf: Timeframe
    close: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    rsi14: float | None = None
    atr14: float | None = None
    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None
    trend: Literal["up", "down", "flat"] | None = None
    bars: int = 0


class MarketContext(BaseModel):
    ts: datetime
    source: str | None = None
    proxy: bool = False
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    spread: float | None = None
    tick_age_seconds: float | None = None
    stale: bool = True
    market_open: bool = False
    session: str = "unknown"
    liquidity_factor: float = 0.0
    timeframes: dict[str, TimeframeState] = Field(default_factory=dict)
    vwap: float | None = None
    vwap_is_twap: bool = False
    atr_m1: float | None = None
    atr_m15: float | None = None
    atr_percentile_90d: float | None = None
    spread_median_30m: float | None = None
    levels: list[Level] = Field(default_factory=lambda: list[Level]())
    asia_high: float | None = None
    asia_low: float | None = None
    prev_day_high: float | None = None
    prev_day_low: float | None = None
    dxy: float | None = None
    dxy_source: str | None = None
    dxy_z_15m: float | None = None
    dxy_z_1h: float | None = None
    us10y: float | None = None
    us10y_change_1d: float | None = None
    real_yield_10y: float | None = None
    real_yield_change_1d: float | None = None
    yields_as_of: str | None = None
    contract_size: float | None = None  # ounces per lot from the broker (MT5); None otherwise
    volume_step: float | None = None
    weekend_drift: float | None = None  # proxy move since Friday's close (Monday gap estimate)
    weekend_reference: float | None = None
    notes: list[str] = Field(default_factory=list)

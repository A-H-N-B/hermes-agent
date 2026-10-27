"""Prices, candles and the market context snapshot handed to the signal engine."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from gold_agent.models.common import Timeframe
from gold_agent.models.physics import MarketPhysics


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
    swing_high: float | None = None  # nearest fractal high above the close (recent bars)
    swing_low: float | None = None  # nearest fractal low below the close
    stoch_k: float | None = None  # Stochastic(5, 3, 3)
    stoch_d: float | None = None
    sar: float | None = None  # Parabolic SAR(0.02, 0.2)
    sar_up: bool | None = None  # SAR below price (uptrend)
    macd: float | None = None  # MACD(12, 26, 9)
    macd_signal: float | None = None
    macd_hist: float | None = None
    adx: float | None = None  # ADX(14): trend strength, any direction
    plus_di: float | None = None
    minus_di: float | None = None
    efficiency: float | None = None  # Kaufman efficiency ratio (20 bars), 0..1
    hurst: float | None = None  # 0.5 random walk, > 0.5 trending, < 0.5 mean-reverting
    kalman_slope: float | None = None  # Kalman trend slope, price units per bar
    kalman_t: float | None = None  # slope / its standard error
    sigma: float | None = None  # next bar's volatility in price units (GARCH, else EWMA)
    sigma_model: Literal["garch", "ewma"] | None = None
    vol_persistence: float | None = None  # GARCH alpha + beta
    vol_pct: float | None = None  # current 20-bar volatility within recent history, 0..1
    regime: Literal["trending", "ranging", "volatile", "normal"] | None = None
    # Econophysics of the chart (market/econophysics.py), from complete bars.
    tail_alpha: float | None = None  # power-law tail index of the moves (~3 in most markets)
    tail_nu: float | None = None  # Student-t d.f. after GARCH; None = normal tails
    entropy: float | None = None  # permutation entropy of the moves: 1 = as patternless as noise
    mf_width: float | None = None  # MF-DFA h(-3) - h(3): intermittency, 0 for a monofractal
    dfa_h: float | None = None  # Hurst exponent by DFA
    ou_t: float | None = None  # Dickey-Fuller t of mean reversion (below -2.87 it is real)
    ou_half_life: float | None = None  # bars to close half the gap to ou_mean (when real)
    ou_mean: float | None = None  # the level price reverts to (when real)
    bars: int = 0


class CrossAsset(BaseModel):
    """A market that moves gold, read from the broker (market/cross_assets.py)."""

    key: str  # DXY, US10Y, USDJPY, XAGUSD, WTI, US500, VIX
    label: str
    symbol: str  # the broker's name, or how a synthetic value was built
    note: str | None = None
    value: float | None = None
    change_15m_pct: float | None = None
    change_1h_pct: float | None = None
    change_1d_pct: float | None = None
    change_5d_pct: float | None = None
    z_15m: float | None = None  # this move vs all 15-minute moves of the last days
    z_1h: float | None = None
    corr_measured: float | None = None  # 5-minute return correlation with gold, last days
    corr: float  # the same, shrunk towards the usual relationship
    pairs: int = 0  # return pairs behind corr_measured
    relevance: float  # 0-1: how much it counts now
    effect: float | None = None  # its pull on gold now, -1 (bearish) .. +1 (bullish)
    weight: float  # its share of the intraday macro score
    why: str


class Impulse(BaseModel):
    """The start of a big move, found the second it happens (market/impulse.py)."""

    side: Literal["BUY", "SELL"]
    z: float  # the move against the per-minute volatility (|z| >= 3 is rare on noise)
    window: int  # minutes of that move
    move: float  # $ over that window, signed
    level: float  # the recent range high (BUY) or low (SELL) it broke
    trigger: float  # the price at which the impulse began: the entry it is measured from
    range_minutes: int  # how far back the broken range reaches
    extension_atr: float | None = None  # price past the trigger, in ATR(14) of the impulse chart
    aligned: bool | None = None  # the M15 trend points the same way (None: no M15 trend yet)
    since: datetime  # when this impulse began


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
    cross: dict[str, CrossAsset] = Field(default_factory=lambda: dict[str, CrossAsset]())
    fundamental_bias: float | None = None  # daily fundamentals, -1 (bearish) .. +1 (bullish)
    fundamental_as_of: str | None = None
    physics: MarketPhysics | None = None  # market-wide econophysics (market/physics.py)
    impulse: Impulse | None = None  # a momentum burst under way (market/impulse.py)
    # Added to every price of a proxy feed so it reads like the user's broker (the user set it
    # from a broker quote, services/market.py); None = no re-basing.
    price_offset: float | None = None
    notes: list[str] = Field(default_factory=list)

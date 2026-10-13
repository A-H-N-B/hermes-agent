"""Market context snapshot (section 9) assembled from the live tick, candles and macro feeds."""

from __future__ import annotations

import statistics
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from gold_agent.config import Settings
from gold_agent.market import indicators as ind
from gold_agent.market.candles import CandleBuilder
from gold_agent.market.levels import asia_range, compute_levels, previous_complete
from gold_agent.market.usd import UsdTracker
from gold_agent.models import Candle, MarketContext, Tick, TimeframeState
from gold_agent.models.common import Timeframe
from gold_agent.timeutils.sessions import SessionClock


@dataclass(frozen=True)
class YieldSnapshot:
    us10y: float | None
    us10y_change_1d: float | None
    real10y: float | None
    real10y_change_1d: float | None
    as_of: str | None


class WeekendTracker:
    """Proxy feeds (PAXG) trade through the weekend: their drift estimates Monday's gap."""

    def __init__(self) -> None:
        self.last_open_price: float | None = None
        self.reference: float | None = None
        self.latest: float | None = None

    def on_tick(self, mid: float, market_open: bool) -> None:
        if market_open:
            self.last_open_price = mid
            self.reference = None
            self.latest = None
            return
        if self.reference is None:
            self.reference = self.last_open_price
        self.latest = mid

    @property
    def drift(self) -> float | None:
        if self.reference is None or self.latest is None:
            return None
        return self.latest - self.reference


def timeframe_state(tf: Timeframe, candles: Sequence[Candle]) -> TimeframeState:
    if not candles:
        return TimeframeState(tf=tf)
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    ema20 = ind.last(ind.ema(closes, 20))
    ema50 = ind.last(ind.ema(closes, 50))
    mid, upper, lower = ind.bollinger(closes)
    trend = None
    if ema20 is not None and ema50 is not None:
        if ema20 > ema50 and closes[-1] > ema50:
            trend = "up"
        elif ema20 < ema50 and closes[-1] < ema50:
            trend = "down"
        else:
            trend = "flat"
    return TimeframeState(
        tf=tf,
        close=closes[-1],
        ema20=ema20,
        ema50=ema50,
        ema200=ind.last(ind.ema(closes, 200)),
        rsi14=ind.last(ind.rsi(closes, 14)),
        atr14=ind.last(ind.atr(highs, lows, closes, 14)),
        bb_upper=ind.last(upper),
        bb_mid=ind.last(mid),
        bb_lower=ind.last(lower),
        trend=trend,
        bars=len(candles),
    )


def atr_percentile(d1: Sequence[Candle], lookback: int = 90) -> float | None:
    done = [c for c in d1 if c.complete]
    if len(done) < 30:
        return None
    series = ind.atr([c.high for c in done], [c.low for c in done], [c.close for c in done], 14)
    values = [float(v) for v in series[-lookback:] if v == v]
    if not values:
        return None
    return ind.percentile_rank(values, values[-1])


def build_market_context(
    *,
    now: datetime,
    tick: Tick | None,
    builder: CandleBuilder,
    sessions: SessionClock,
    settings: Settings,
    spreads: deque[tuple[datetime, float]],
    usd: UsdTracker | None = None,
    yields: YieldSnapshot | None = None,
    weekend: WeekendTracker | None = None,
) -> MarketContext:
    status = sessions.status(now)
    notes: list[str] = []
    base = MarketContext(
        ts=now,
        market_open=status.open,
        session=status.session,
        liquidity_factor=status.liquidity_factor,
    )
    if tick is None:
        return base.model_copy(update={"notes": ["data harga tidak tersedia"]})
    age = (now - tick.received_at).total_seconds()
    stale = age > settings.price_feed.stale_tick_seconds
    if tick.proxy:
        notes.append(f"harga dari {tick.source} (proxy, bukan XAUUSD spot)")
    if tick.spread <= 0:
        notes.append("spread tidak tersedia dari feed ini")
    states = {
        tf: timeframe_state(tf, builder.candles(tf)) for tf in ("M5", "M15", "H1", "H4", "D1")
    }
    m1 = builder.candles("M1")
    atr_m1 = ind.last(ind.atr([c.high for c in m1], [c.low for c in m1], [c.close for c in m1], 14))
    day_start = sessions.trading_day_start(now)
    session_bars = [c for c in m1 if c.open_time >= day_start]
    vwap, is_twap = ind.vwap(
        [c.high for c in session_bars],
        [c.low for c in session_bars],
        [c.close for c in session_bars],
        [c.volume for c in session_bars],
    )
    if is_twap:
        notes.append("VWAP memakai TWAP (feed tanpa volume)")
    window_start = now - timedelta(minutes=settings.market.spread_window_minutes)
    recent_spreads = [s for ts, s in spreads if ts >= window_start and s > 0]
    asia = asia_range(builder.candles("M5"), now, sessions)
    d1 = builder.candles("D1")
    prev_day = previous_complete(d1, now)
    levels = compute_levels(
        price=tick.mid,
        now=now,
        d1=d1,
        h1=builder.candles("H1"),
        asia=asia,
        sessions=sessions,
        round_steps=settings.market.round_levels,
        swing_half_width=settings.market.swing_half_width,
    )
    dxy_z_15 = usd.zscore(now, 15) if usd else None
    dxy_z_60 = usd.zscore(now, 60) if usd else None
    if usd is None or usd.latest is None:
        notes.append("data USD/DXY tidak tersedia")
    if yields is None or yields.us10y is None:
        notes.append("data yield tidak tersedia")
    return base.model_copy(
        update={
            "source": tick.source,
            "proxy": tick.proxy,
            "bid": tick.bid,
            "ask": tick.ask,
            "mid": tick.mid,
            "spread": tick.spread,
            "tick_age_seconds": round(age, 3),
            "stale": stale,
            "timeframes": states,
            "vwap": vwap,
            "vwap_is_twap": is_twap,
            "atr_m1": atr_m1,
            "atr_m15": states["M15"].atr14,
            "atr_percentile_90d": atr_percentile(d1),
            "spread_median_30m": statistics.median(recent_spreads) if recent_spreads else None,
            "levels": levels,
            "asia_high": asia[0] if asia else None,
            "asia_low": asia[1] if asia else None,
            "prev_day_high": prev_day.high if prev_day else None,
            "prev_day_low": prev_day.low if prev_day else None,
            "dxy": usd.latest if usd else None,
            "dxy_source": usd.source if usd else None,
            "dxy_z_15m": dxy_z_15,
            "dxy_z_1h": dxy_z_60,
            "us10y": yields.us10y if yields else None,
            "us10y_change_1d": yields.us10y_change_1d if yields else None,
            "real_yield_10y": yields.real10y if yields else None,
            "real_yield_change_1d": yields.real10y_change_1d if yields else None,
            "yields_as_of": yields.as_of if yields else None,
            "weekend_drift": weekend.drift if weekend else None,
            "weekend_reference": weekend.reference if weekend else None,
            "notes": notes,
        }
    )

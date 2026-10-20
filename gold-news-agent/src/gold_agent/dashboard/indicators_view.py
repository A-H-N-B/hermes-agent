"""Indicators for the Live page: per-bar overlays for the chart and a per-timeframe BUY/SELL read.

The overlays use the same numpy indicators as the market service (EMA, Bollinger, RSI), computed on
extra warm-up bars so EMA200 is defined from the first bar shown. The timeframe read is a plain
vote of the market service's own TimeframeState, shown beside the agent's signal: it describes the
chart, it never creates a signal (only the engine does).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from gold_agent.market import indicators as ind
from gold_agent.market.levels import nearest_above, nearest_below
from gold_agent.models import Candle, MarketContext
from gold_agent.models.market import TimeframeState

JSON = dict[str, Any]
WARMUP_BARS = 200  # EMA200 needs this many bars before its first value
SUMMARY_TFS = ("M5", "M15", "H1", "H4")
RSI_BULL, RSI_BEAR = 55.0, 45.0
STOCH = (5, 3, 3)  # Stoch(5, 3, 3)
SAR = (0.02, 0.20)  # Parabolic SAR(start/step 0.02, maximum 0.20)
STOCH_HIGH, STOCH_LOW = 80.0, 20.0


def _num(value: float) -> float | None:
    return None if math.isnan(value) else round(value, 2)


def indicator_series(bars: Sequence[Candle], keep: int) -> list[JSON]:
    """EMA20/50/200, Bollinger(20, 2), RSI14, Stoch(5,3,3) and SAR(0.02, 0.2) per bar; only the
    last `keep` bars are returned."""
    closes = [bar.close for bar in bars]
    highs, lows = [bar.high for bar in bars], [bar.low for bar in bars]
    stoch_k, stoch_d = ind.stochastic(highs, lows, closes, *STOCH)
    sar, sar_dir = ind.parabolic_sar(highs, lows, *SAR)
    ema20, ema50, ema200 = ind.ema(closes, 20), ind.ema(closes, 50), ind.ema(closes, 200)
    bb_mid, bb_upper, bb_lower = ind.bollinger(closes, 20, 2.0)
    rsi14 = ind.rsi(closes, 14)
    rows: list[JSON] = []
    for i in range(max(0, len(bars) - keep), len(bars)):
        rows.append(
            {
                "time": int(bars[i].open_time.timestamp()),
                "ema20": _num(ema20[i]),
                "ema50": _num(ema50[i]),
                "ema200": _num(ema200[i]),
                "bb_upper": _num(bb_upper[i]),
                "bb_mid": _num(bb_mid[i]),
                "bb_lower": _num(bb_lower[i]),
                "rsi14": _num(rsi14[i]),
                "stoch_k": _num(stoch_k[i]),
                "stoch_d": _num(stoch_d[i]),
                "sar": _num(sar[i]),
                "sar_up": bool(sar_dir[i] > 0) if not math.isnan(sar[i]) else None,
            }
        )
    return rows


def momentum(bars: Sequence[Candle]) -> JSON:
    """Latest Stoch(5,3,3) and SAR(0.02, 0.2) of one timeframe's candles (None without data)."""
    closes = [bar.close for bar in bars]
    highs, lows = [bar.high for bar in bars], [bar.low for bar in bars]
    stoch_k, stoch_d = ind.stochastic(highs, lows, closes, *STOCH)
    sar, sar_dir = ind.parabolic_sar(highs, lows, *SAR)
    ok = len(bars) > 0 and not math.isnan(sar[-1])
    return {
        "stoch_k": _num(stoch_k[-1]) if len(bars) else None,
        "stoch_d": _num(stoch_d[-1]) if len(bars) else None,
        "sar": _num(sar[-1]) if ok else None,
        "sar_up": bool(sar_dir[-1] > 0) if ok else None,
    }


def _momentum_votes(extra: JSON) -> list[tuple[int, str]]:
    votes: list[tuple[int, str]] = []
    if extra.get("sar_up") is not None:
        votes.append((1, "SAR di bawah harga") if extra["sar_up"] else (-1, "SAR di atas harga"))
    k, d = extra.get("stoch_k"), extra.get("stoch_d")
    if k is not None and d is not None:
        if k > d:
            votes.append((1, f"Stoch naik ({k:.0f})" + (", jenuh beli" if k >= STOCH_HIGH else "")))
        elif k < d:
            votes.append(
                (-1, f"Stoch turun ({k:.0f})" + (", jenuh jual" if k <= STOCH_LOW else ""))
            )
    return votes


def timeframe_read(state: TimeframeState, extra: JSON | None = None) -> JSON:
    """Vote of trend, price vs EMA20/EMA200, EMA20 vs EMA50, RSI, and (when given) Parabolic SAR
    and Stochastic: +1 each for up, -1 for down."""
    votes: list[tuple[int, str]] = []
    close = state.close
    if state.trend in ("up", "down"):
        votes.append((1 if state.trend == "up" else -1, f"tren {state.trend}"))
    if close is not None and state.ema20 is not None:
        votes.append((1, "harga > EMA20") if close > state.ema20 else (-1, "harga < EMA20"))
    if state.ema20 is not None and state.ema50 is not None:
        votes.append((1, "EMA20 > EMA50") if state.ema20 > state.ema50 else (-1, "EMA20 < EMA50"))
    if close is not None and state.ema200 is not None:
        votes.append((1, "harga > EMA200") if close > state.ema200 else (-1, "harga < EMA200"))
    if state.rsi14 is not None:
        if state.rsi14 >= RSI_BULL:
            votes.append((1, f"RSI {state.rsi14:.0f}"))
        elif state.rsi14 <= RSI_BEAR:
            votes.append((-1, f"RSI {state.rsi14:.0f}"))
    votes += _momentum_votes(extra or {})
    score = sum(v for v, _ in votes)
    strong = len(votes) >= 4 and abs(score) == len(votes)
    if score >= 2:
        verdict = "BUY KUAT" if strong else "BUY"
    elif score <= -2:
        verdict = "SELL KUAT" if strong else "SELL"
    else:
        verdict = "NETRAL"
    return {
        "tf": state.tf,
        "verdict": verdict,
        "score": score,
        "votes": len(votes),
        "reasons": [reason for _, reason in votes],
        "close": state.close,
        "ema20": state.ema20,
        "ema50": state.ema50,
        "ema200": state.ema200,
        "rsi14": state.rsi14,
        "atr14": state.atr14,
        **{key: (extra or {}).get(key) for key in ("stoch_k", "stoch_d", "sar", "sar_up")},
    }


def indicator_summary(context: MarketContext | None, extras: dict[str, JSON] | None = None) -> JSON:
    if context is None:
        return {"timeframes": [], "overall": None, "levels": [], "vwap": None}
    extras = extras or {}
    reads = [
        timeframe_read(context.timeframes[tf], extras.get(tf))
        for tf in SUMMARY_TFS
        if tf in context.timeframes
    ]
    buys = sum(1 for r in reads if r["verdict"].startswith("BUY"))
    sells = sum(1 for r in reads if r["verdict"].startswith("SELL"))
    overall = (
        "BUY" if buys > sells and buys >= 2 else "SELL" if sells > buys and sells >= 2 else "NETRAL"
    )
    levels = [
        {"name": level.name, "price": level.price, "kind": level.kind} for level in context.levels
    ]
    for name, price in (
        ("High Asia", context.asia_high),
        ("Low Asia", context.asia_low),
        ("High kemarin", context.prev_day_high),
        ("Low kemarin", context.prev_day_low),
    ):
        if price is not None:
            levels.append({"name": name, "price": price, "kind": "range"})
    above = nearest_above(context.levels, context.mid) if context.mid is not None else None
    below = nearest_below(context.levels, context.mid) if context.mid is not None else None
    return {
        "timeframes": reads,
        "overall": {"verdict": overall, "buy": buys, "sell": sells, "of": len(reads)},
        "levels": levels,
        "nearest_above": {"name": above.name, "price": above.price} if above else None,
        "nearest_below": {"name": below.name, "price": below.price} if below else None,
        "vwap": context.vwap,
        "vwap_is_twap": context.vwap_is_twap,
        "atr_m15": context.atr_m15,
        "dxy_z_1h": context.dxy_z_1h,
    }

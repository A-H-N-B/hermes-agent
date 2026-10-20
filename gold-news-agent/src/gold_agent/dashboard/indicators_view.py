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


def _num(value: float) -> float | None:
    return None if math.isnan(value) else round(value, 2)


def indicator_series(bars: Sequence[Candle], keep: int) -> list[JSON]:
    """EMA20/50/200, Bollinger(20, 2) and RSI14 per bar; only the last `keep` bars are returned."""
    closes = [bar.close for bar in bars]
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
            }
        )
    return rows


def timeframe_read(state: TimeframeState) -> JSON:
    """Vote of trend, price vs EMA20/EMA200, EMA20 vs EMA50 and RSI: +1 each for up, -1 for down."""
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
    }


def indicator_summary(context: MarketContext | None) -> JSON:
    if context is None:
        return {"timeframes": [], "overall": None, "levels": [], "vwap": None}
    reads = [
        timeframe_read(context.timeframes[tf]) for tf in SUMMARY_TFS if tf in context.timeframes
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

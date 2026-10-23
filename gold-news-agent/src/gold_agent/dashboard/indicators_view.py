"""Indicators for the Live page: per-bar overlays for the chart and the per-timeframe summary.

The overlays use the same numpy indicators as the market service (EMA, Bollinger, RSI), computed on
extra warm-up bars so EMA200 is defined from the first bar shown. The per-timeframe BUY/SELL read
is `market/reads.py` (with the chart statistics of market/quant.py), shown beside the agent's
signal; it never creates a signal. The summary also carries the related markets read from the
broker, the macro score they make, and the day's fundamentals.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from gold_agent.market import indicators as ind
from gold_agent.market.levels import nearest_above, nearest_below
from gold_agent.market.physics_read import market_rows
from gold_agent.market.reads import SAR, STOCH, timeframe_read
from gold_agent.models import Candle, FundamentalSnapshot, MarketContext
from gold_agent.signals.scoring import macro_component, macro_parts

JSON = dict[str, Any]
WARMUP_BARS = 200  # EMA200 needs this many bars before its first value
SUMMARY_TFS = ("M5", "M15", "H1", "H4")


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


def drivers(context: MarketContext, fundamentals: FundamentalSnapshot | None) -> JSON:
    """Related markets, the macro score they make (M), and the day's fundamentals."""
    value, available = macro_component(context)
    parts = macro_parts(context)
    total = sum(weight for _, weight, _ in parts) or 1.0
    return {
        "cross": [asset.model_dump(mode="json") for asset in context.cross.values()],
        "macro": {
            "value": round(value, 3) if available else None,
            "parts": [
                {"label": label, "pull": round(weight * pull / total, 3)}
                for label, weight, pull in sorted(parts, key=lambda p: -abs(p[1] * p[2]))
            ],
        },
        "fundamentals": fundamentals.model_dump(mode="json") if fundamentals else None,
        "physics": market_rows(context.physics),
    }


def indicator_summary(
    context: MarketContext | None,
    extras: dict[str, JSON] | None = None,
    fundamentals: FundamentalSnapshot | None = None,
) -> JSON:
    if context is None:
        return {
            "timeframes": [],
            "overall": None,
            "levels": [],
            "vwap": None,
            "cross": [],
            "macro": {"value": None, "parts": []},
            "fundamentals": fundamentals.model_dump(mode="json") if fundamentals else None,
            "physics": [],
        }
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
        **drivers(context, fundamentals),
    }

"""Per-timeframe read of the chart: a plain vote of trend, EMAs, RSI, MACD(12,26,9), the
Kalman trend (only when statistically clear, |t| >= 2), Stochastic(5,3,3) and Parabolic
SAR(0.02, 0.2). It describes the chart, it never creates a signal (only the engine does); the
next-candle forecast, its walk-forward score and the Live page all use this one vote.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from gold_agent.market import indicators as ind
from gold_agent.models import Candle
from gold_agent.models.market import TimeframeState

JSON = dict[str, Any]
RSI_BULL, RSI_BEAR = 55.0, 45.0
STOCH = (5, 3, 3)  # Stoch(5, 3, 3)
SAR = (0.02, 0.20)  # Parabolic SAR(start/step 0.02, maximum 0.20)
STOCH_HIGH, STOCH_LOW = 80.0, 20.0
KALMAN_T = 2.0  # a slope this many standard errors from zero is a trend, not noise


def _num(value: float) -> float | None:
    return None if math.isnan(value) else round(value, 2)


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
    if state.macd_hist:
        votes.append(
            (1, "MACD di atas sinyal") if state.macd_hist > 0 else (-1, "MACD di bawah sinyal")
        )
    if state.kalman_t is not None and abs(state.kalman_t) >= KALMAN_T:
        rising = state.kalman_t > 0
        votes.append(
            (
                1 if rising else -1,
                f"tren Kalman {'naik' if rising else 'turun'} (t={state.kalman_t:.1f})",
            )
        )
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
        "macd": state.macd,
        "macd_signal": state.macd_signal,
        "macd_hist": state.macd_hist,
        "adx": state.adx,
        "plus_di": state.plus_di,
        "minus_di": state.minus_di,
        "efficiency": state.efficiency,
        "hurst": state.hurst,
        "kalman_t": state.kalman_t,
        "sigma": state.sigma,
        "vol_pct": state.vol_pct,
        "regime": state.regime,
        **{key: (extra or {}).get(key) for key in ("stoch_k", "stoch_d", "sar", "sar_up")},
    }

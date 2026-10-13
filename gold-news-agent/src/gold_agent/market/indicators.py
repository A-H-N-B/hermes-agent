"""Technical indicators implemented with numpy (section 9). NaN marks "not enough data"."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


def _arr(values: Sequence[float]) -> Array:
    return np.asarray(values, dtype=np.float64)


def sma(values: Sequence[float], period: int) -> Array:
    data = _arr(values)
    out = np.full(data.shape, np.nan)
    if len(data) >= period:
        cumsum = np.cumsum(np.insert(data, 0, 0.0))
        out[period - 1 :] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def ema(values: Sequence[float], period: int) -> Array:
    """Exponential MA, alpha = 2/(n+1), seeded with the SMA of the first n values."""
    data = _arr(values)
    out = np.full(data.shape, np.nan)
    if len(data) < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = data[:period].mean()
    for i in range(period, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out


def rma(values: Sequence[float], period: int) -> Array:
    """Wilder's smoothing (alpha = 1/n), seeded with the SMA of the first n values."""
    data = _arr(values)
    out = np.full(data.shape, np.nan)
    if len(data) < period:
        return out
    out[period - 1] = data[:period].mean()
    for i in range(period, len(data)):
        out[i] = (out[i - 1] * (period - 1) + data[i]) / period
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Array:
    data = _arr(closes)
    out = np.full(data.shape, np.nan)
    if len(data) <= period:
        return out
    delta = np.diff(data)
    gains = rma(np.clip(delta, 0, None).tolist(), period)
    losses = rma(np.clip(-delta, 0, None).tolist(), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = gains / losses
        values = 100 - 100 / (1 + rs)
    values = np.where(losses == 0, np.where(gains == 0, 50.0, 100.0), values)
    out[1:] = values
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> Array:
    h, lo, c = _arr(high), _arr(low), _arr(close)
    prev_close = np.concatenate(([np.nan], c[:-1]))
    ranges = np.vstack([h - lo, np.abs(h - prev_close), np.abs(lo - prev_close)])
    tr = np.nanmax(ranges, axis=0)
    if len(tr):
        tr[0] = h[0] - lo[0]
    return tr


def atr(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14
) -> Array:
    return rma(true_range(high, low, close).tolist(), period)


def bollinger(
    closes: Sequence[float], period: int = 20, mult: float = 2.0
) -> tuple[Array, Array, Array]:
    """(middle, upper, lower) with population standard deviation, as charting platforms use."""
    data = _arr(closes)
    mid = sma(closes, period)
    std = np.full(data.shape, np.nan)
    for i in range(period - 1, len(data)):
        std[i] = data[i - period + 1 : i + 1].std(ddof=0)
    return mid, mid + mult * std, mid - mult * std


def vwap(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], volume: Sequence[float]
) -> tuple[float | None, bool]:
    """Session VWAP of the given candles; falls back to TWAP (flag True) without volume."""
    if not close:
        return None, False
    typical = (_arr(high) + _arr(low) + _arr(close)) / 3
    vol = _arr(volume)
    if float(vol.sum()) <= 0:
        return float(typical.mean()), True
    return float((typical * vol).sum() / vol.sum()), False


def pivots_classic(high: float, low: float, close: float) -> dict[str, float]:
    p = (high + low + close) / 3
    return {
        "P": p,
        "R1": 2 * p - low,
        "S1": 2 * p - high,
        "R2": p + (high - low),
        "S2": p - (high - low),
        "R3": high + 2 * (p - low),
        "S3": low - 2 * (high - p),
    }


def swing_points(
    highs: Sequence[float], lows: Sequence[float], half_width: int
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Fractal swing highs/lows: a bar higher (lower) than `half_width` bars on each side."""
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for i in range(half_width, len(highs) - half_width):
        window_h = highs[i - half_width : i + half_width + 1]
        window_l = lows[i - half_width : i + half_width + 1]
        if highs[i] == max(window_h) and window_h.count(highs[i]) == 1:
            swing_highs.append((i, highs[i]))
        if lows[i] == min(window_l) and window_l.count(lows[i]) == 1:
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def percentile_rank(series: Sequence[float], value: float) -> float | None:
    clean = [v for v in series if not math.isnan(v)]
    if not clean:
        return None
    below = sum(1 for v in clean if v < value)
    equal = sum(1 for v in clean if v == value)
    return 100.0 * (below + 0.5 * equal) / len(clean)


def last(values: Array) -> float | None:
    """Last non-NaN value."""
    for value in values[::-1]:
        if not math.isnan(float(value)):
            return float(value)
    return None

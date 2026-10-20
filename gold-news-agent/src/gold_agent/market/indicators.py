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
    if len(data) >= period:
        std[period - 1 :] = np.lib.stride_tricks.sliding_window_view(data, period).std(axis=1)
    return mid, mid + mult * std, mid - mult * std


def stochastic(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    k_period: int = 5,
    k_smooth: int = 3,
    d_period: int = 3,
) -> tuple[Array, Array]:
    """Slow stochastic (%K, %D) as charting platforms show Stoch(5, 3, 3): raw %K over k_period,
    %K = SMA(k_smooth) of raw, %D = SMA(d_period) of %K. A flat window counts as 50."""
    h, lo, c = _arr(high), _arr(low), _arr(close)
    raw = np.full(c.shape, np.nan)
    if len(c) >= k_period:
        highest = np.lib.stride_tricks.sliding_window_view(h, k_period).max(axis=1)
        lowest = np.lib.stride_tricks.sliding_window_view(lo, k_period).min(axis=1)
        span = highest - lowest
        with np.errstate(divide="ignore", invalid="ignore"):
            value = np.where(span > 0, 100 * (c[k_period - 1 :] - lowest) / span, 50.0)
        raw[k_period - 1 :] = value
    k = _sma_nan(raw, k_smooth)
    return k, _sma_nan(k, d_period)


def _sma_nan(values: Array, period: int) -> Array:
    """SMA that starts after the leading NaNs of `values` (for chained smoothing)."""
    out = np.full(values.shape, np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) == 0:
        return out
    first = int(valid[0])
    out[first:] = sma(values[first:].tolist(), period)
    return out


def parabolic_sar(
    high: Sequence[float],
    low: Sequence[float],
    start: float = 0.02,
    maximum: float = 0.2,
    increment: float | None = None,
) -> tuple[Array, Array]:
    """Wilder's Parabolic SAR (start, maximum; the step defaults to `start`, as platforms do).

    Returns (sar, direction) with direction +1 while the SAR sits below price (uptrend) and -1
    above it. The SAR never enters the previous two bars' range, and flips when price crosses it.
    """
    h, lo = _arr(high), _arr(low)
    step = start if increment is None else increment
    n = len(h)
    sar = np.full(n, np.nan)
    direction = np.zeros(n)
    if n < 2:
        return sar, direction
    up = h[1] >= h[0]
    af = start
    extreme = h[0] if up else lo[0]
    sar[1] = lo[0] if up else h[0]
    direction[1] = 1 if up else -1
    for i in range(2, n):
        prev = sar[i - 1]
        value = prev + af * (extreme - prev)
        if up:
            value = min(value, lo[i - 1], lo[i - 2])
            if lo[i] < value:  # reversal to a downtrend
                up, value, extreme, af = False, extreme, lo[i], start
            elif h[i] > extreme:
                extreme, af = h[i], min(af + step, maximum)
        else:
            value = max(value, h[i - 1], h[i - 2])
            if h[i] > value:  # reversal to an uptrend
                up, value, extreme, af = True, extreme, h[i], start
            elif lo[i] < extreme:
                extreme, af = lo[i], min(af + step, maximum)
        sar[i] = value
        direction[i] = 1 if up else -1
    return sar, direction


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

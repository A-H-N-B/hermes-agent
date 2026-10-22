"""Statistical reads of a price series beyond the classic indicators (numpy only).

Volatility
    EWMA (RiskMetrics, lambda 0.94) and GARCH(1,1) fitted by maximum likelihood with variance
    targeting (grid over alpha/beta). GARCH gives the next bars' volatility, which clusters:
    a quiet market stays quiet, a wild one stays wild for a while.
Trend strength
    Kaufman efficiency ratio (net move / path travelled), the Hurst exponent (how the spread of
    k-bar moves grows with k: 0.5 random walk, above trending, below mean-reverting) and a
    Kalman filter of the local drift whose t-statistic is calibrated on a random walk.
Regime
    trending / ranging / volatile / normal, from the reads above and ADX.
First passage
    The chance price reaches a target before a stop: closed form for Brownian motion with
    drift, and a Monte Carlo over the trade's validity window with GARCH volatility, so a target
    that is unlikely to be reached in time is not counted as reached.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
Regime = Literal["trending", "ranging", "volatile", "normal"]
REGIME_ID: dict[str, str] = {
    "trending": "tren kuat",
    "ranging": "sideways",
    "volatile": "sangat volatil",
    "normal": "normal",
}


def log_returns(closes: Sequence[float]) -> Array:
    data = np.asarray(closes, dtype=np.float64)
    data = data[data > 0]
    return np.diff(np.log(data)) if len(data) > 1 else np.zeros(0)


def ewma_sigma(returns: Array, lam: float = 0.94) -> float | None:
    """RiskMetrics volatility of the next return (per bar, as a log return)."""
    if len(returns) < 20:
        return None
    var = float(np.mean(returns[:20] ** 2))
    for r in returns[20:]:
        var = lam * var + (1 - lam) * float(r) ** 2
    return math.sqrt(var)


@dataclass(frozen=True)
class Garch:
    omega: float
    alpha: float
    beta: float
    next_var: float  # variance of the next return
    long_var: float  # where variance mean-reverts to

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    def horizon_sigma(self, bars: int) -> float:
        """Volatility of the sum of the next `bars` returns."""
        total = sum(
            self.long_var + self.persistence ** (k - 1) * (self.next_var - self.long_var)
            for k in range(1, bars + 1)
        )
        return math.sqrt(max(total, 0.0))

    def path_vars(self, bars: int) -> Array:
        k = np.arange(1, bars + 1, dtype=np.float64)
        return np.maximum(
            self.long_var + self.persistence ** (k - 1) * (self.next_var - self.long_var), 1e-18
        )


_ALPHAS = np.linspace(0.02, 0.25, 12)
_BETAS = np.linspace(0.60, 0.975, 16)


def garch11(returns: Array) -> Garch | None:
    """GARCH(1,1) by Gaussian maximum likelihood with variance targeting (omega fixed so the
    long-run variance equals the sample's), over a grid of alpha/beta with alpha+beta < 1."""
    if len(returns) < 200:
        return None
    r = returns - returns.mean()
    target = float(r.var())
    if target <= 0:
        return None
    alpha, beta = np.meshgrid(_ALPHAS, _BETAS)
    alpha, beta = alpha.ravel(), beta.ravel()
    keep = alpha + beta < 0.995
    alpha, beta = alpha[keep], beta[keep]
    omega = target * (1 - alpha - beta)
    r2 = r**2
    h = np.full(alpha.shape, target)
    loglik = np.zeros(alpha.shape)
    for t in range(1, len(r)):
        h = omega + alpha * r2[t - 1] + beta * h
        loglik -= 0.5 * (np.log(h) + r2[t] / h)
    best = int(np.argmax(loglik))
    a, b, w = float(alpha[best]), float(beta[best]), float(omega[best])
    var = target
    for t in range(1, len(r)):
        var = w + a * float(r2[t - 1]) + b * var
    next_var = w + a * float(r2[-1]) + b * var
    return Garch(omega=w, alpha=a, beta=b, next_var=next_var, long_var=target)


def efficiency_ratio(closes: Sequence[float], n: int = 20) -> float | None:
    if len(closes) <= n:
        return None
    window = np.asarray(closes[-(n + 1) :], dtype=np.float64)
    path = float(np.abs(np.diff(window)).sum())
    return abs(float(window[-1] - window[0])) / path if path > 0 else 0.0


def hurst(closes: Sequence[float], lags: Sequence[int] = (2, 4, 8, 16, 32)) -> float | None:
    """Slope of log(std of k-bar log moves) on log(k)."""
    if len(closes) < 8 * max(lags):
        return None
    x = np.log(np.asarray(closes, dtype=np.float64))
    spreads = [float(np.std(x[k:] - x[:-k])) for k in lags]
    if min(spreads) <= 0:
        return None
    slope = float(np.polyfit(np.log(lags), np.log(spreads), 1)[0])
    return max(0.0, min(1.0, slope))


@dataclass(frozen=True)
class KalmanTrend:
    level: float
    slope: float  # per bar, in price units
    slope_se: float

    @property
    def t(self) -> float:
        return self.slope / self.slope_se if self.slope_se > 0 else 0.0


KALMAN_WARMUP = 50
KALMAN_Q = 1e-3  # how fast the drift may wander, relative to the bar-to-bar variance


def kalman_series(closes: Sequence[float]) -> tuple[Array, Array, Array]:
    """Kalman filter of the local drift, run causally over the closes: (close, drift per bar,
    standard error of that drift if there were no trend), NaN during the warm-up.

    Each bar's move = drift + noise, the drift a slow random walk (variance KALMAN_Q times the
    move variance). The standard error is the estimate's spread under pure noise (a random
    walk), tracked through the same gains, so drift / error is a calibrated t-statistic:
    |t| >= 2 happens about 5% of the time on a market without a trend."""
    data = np.asarray(closes, dtype=np.float64)
    n = len(data)
    level, slope, se = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    if n < KALMAN_WARMUP:
        return level, slope, se
    moves = np.diff(data)
    r = float(np.var(moves))
    if r <= 0:
        return level, slope, se
    q = KALMAN_Q * r
    drift, p, null_var = 0.0, r, 0.0
    drifts: list[float] = []
    errors: list[float] = []
    for i, move in enumerate(moves.tolist(), start=1):
        p += q
        gain = p / (p + r)
        drift += gain * (move - drift)
        p *= 1 - gain
        null_var = (1 - gain) ** 2 * null_var + gain * gain * r
        if i >= KALMAN_WARMUP - 1:
            drifts.append(drift)
            errors.append(math.sqrt(null_var))
    first = KALMAN_WARMUP - 1
    level[first:], slope[first:], se[first:] = data[first:], drifts, errors
    return level, slope, se


def kalman_trend(closes: Sequence[float]) -> KalmanTrend | None:
    level, slope, se = kalman_series(closes)
    if not len(slope) or math.isnan(float(slope[-1])):
        return None
    return KalmanTrend(level=float(level[-1]), slope=float(slope[-1]), slope_se=float(se[-1]))


# Thresholds set on random walks (600 bars): each label fires on roughly 5% of trendless
# markets, so "tren kuat" or "sideways" means the chart is unlikely to be noise.
TREND_ER = 0.55  # efficiency ratio, 95th percentile of a random walk
TREND_ADX, TREND_HURST = 30.0, 0.55
RANGE_HURST, RANGE_ADX = 0.42, 20.0  # Hurst 5th percentile of a random walk: mean reversion
VOLATILE_PCT = 0.9


def classify(
    er: float | None, hurst_value: float | None, adx: float | None, vol_pct: float | None
) -> Regime:
    if vol_pct is not None and vol_pct >= VOLATILE_PCT:
        return "volatile"
    if (er is not None and er >= TREND_ER) or (
        adx is not None
        and adx >= TREND_ADX
        and hurst_value is not None
        and hurst_value >= TREND_HURST
    ):
        return "trending"
    if hurst_value is not None and hurst_value <= RANGE_HURST and (adx is None or adx < RANGE_ADX):
        return "ranging"
    return "normal"


# ------------------------------------------------------------------ first passage


def first_passage(up: float, down: float, mu: float, sigma: float) -> float:
    """P(reaching +up before -down) for X_t = mu t + sigma W_t (no time limit)."""
    if up <= 0:
        return 1.0
    if down <= 0:
        return 0.0
    if sigma <= 0:
        return 1.0 if mu > 0 else 0.0 if mu < 0 else down / (up + down)
    k = 2 * mu / sigma**2
    if abs(k * (up + down)) < 1e-9:
        return down / (up + down)
    k = max(-700 / (up + down), min(700 / (up + down), k))
    return math.expm1(-k * down) / math.expm1(-k * (up + down))


def drift_from_hit_rate(p: float, sigma: float, bars: int = 1) -> float:
    """Per-bar drift that makes a `bars`-ahead call right with probability p."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return sigma * NormalDist().inv_cdf(p) / math.sqrt(bars)


@dataclass(frozen=True)
class Passage:
    target: float  # P(target first)
    stop: float  # P(stop first)
    neither: float  # P(neither before the window ends)


def simulate_passage(
    up: float,
    down: float,
    mu: float,
    bar_vars: Sequence[float],
    *,
    substeps: int = 12,
    paths: int = 4000,
    seed: int = 7,
) -> Passage:
    """Monte Carlo of Brownian paths over the window (one variance per bar, e.g. GARCH's term
    structure), checking the barriers `substeps` times per bar. Antithetic pairs halve the noise;
    the fixed seed keeps the answer identical for identical inputs (replays)."""
    rng = np.random.default_rng(seed)
    half = paths // 2
    alive = np.ones(2 * half, dtype=bool)
    hit_up = np.zeros(2 * half, dtype=bool)
    hit_down = np.zeros(2 * half, dtype=bool)
    x = np.zeros(2 * half)
    for var in bar_vars:
        step_sd = math.sqrt(max(var, 0.0) / substeps)
        for _ in range(substeps):
            shocks = rng.standard_normal(half)
            x = x + mu / substeps + step_sd * np.concatenate([shocks, -shocks])
            up_now = alive & (x >= up)
            down_now = alive & (x <= -down)
            hit_up |= up_now
            hit_down |= down_now
            alive &= ~(up_now | down_now)
    n = 2 * half
    return Passage(
        target=float(hit_up.sum()) / n,
        stop=float(hit_down.sum()) / n,
        neither=float(alive.sum()) / n,
    )


# ------------------------------------------------------------------ per-timeframe reads


@dataclass(frozen=True)
class SlowReads:
    """The reads that only change when a bar completes (cached per timeframe and bar)."""

    sigma: float | None  # next bar's volatility in price units
    model: Literal["garch", "ewma"] | None
    persistence: float | None
    hurst: float | None
    vol_pct: float | None  # current 20-bar volatility within this history, 0..1


_SLOW_CACHE: dict[tuple[str, float, int, float], SlowReads] = {}
_SLOW_CACHE_SIZE = 64


def slow_reads(tf: str, last_open: float, closes: Sequence[float]) -> SlowReads:
    """GARCH (EWMA when history is short), Hurst and the volatility percentile over complete
    bars, recomputed only when a new bar completes (`last_open`: the newest bar's open time)."""
    if not closes:
        return SlowReads(None, None, None, None, None)
    key = (tf, last_open, len(closes), closes[-1])
    cached = _SLOW_CACHE.get(key)
    if cached is not None:
        return cached
    returns = log_returns(closes)
    garch = garch11(returns)
    price = closes[-1]
    if garch is not None:
        sigma, model, persistence = math.sqrt(garch.next_var) * price, "garch", garch.persistence
    else:
        ewma = ewma_sigma(returns)
        sigma, model, persistence = (ewma * price if ewma else None), "ewma", None
    vol_pct = None
    if len(returns) >= 60:
        rolling = np.lib.stride_tricks.sliding_window_view(returns, 20).std(axis=1)
        vol_pct = float((rolling < rolling[-1]).mean())
    reads = SlowReads(
        sigma=sigma,
        model=model if sigma else None,
        persistence=persistence,
        hurst=hurst(closes),
        vol_pct=vol_pct,
    )
    if len(_SLOW_CACHE) >= _SLOW_CACHE_SIZE:
        _SLOW_CACHE.pop(next(iter(_SLOW_CACHE)))
    _SLOW_CACHE[key] = reads
    return reads

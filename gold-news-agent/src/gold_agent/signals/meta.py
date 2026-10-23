"""Meta-labeling (Lopez de Prado): a second model that learns from this agent's own past setups on
a chart in which conditions they reached TP1 before the stop.

The engine (the primary model) says BUY or SELL; this one says how likely that call is to pay,
given the chart's state at entry: the trend's t-statistic, the stretch from EMA20, RSI, MACD,
ADX, efficiency, the volatility percentile, permutation entropy, Hurst, multifractal width, the
tail index, the mean-reversion t and the hour. Each past setup's features come from
`timeframe_state` over the same window of bars the live chart holds, so training and live reads
cannot drift apart.

L2-regularised logistic regression by Newton's method. Walk-forward: each setup is predicted by
a model trained only on setups whose outcome was known before it was entered, and scored against
the base rate of those same setups (Brier score). The model counts only once it beats the base
rate beyond chance (Diebold-Mariano t >= PROVEN_T on >= MIN_EVALUATED predictions); until then
its probability is shown and not used.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gold_agent.market.context import timeframe_state
from gold_agent.models import Candle, TimeframeState
from gold_agent.models.common import Timeframe
from gold_agent.signals.setups import Setup
from gold_agent.timeutils.sessions import to_wib

JSON = dict[str, Any]
Array = NDArray[np.float64]
FEATURES = (
    "trend_t",
    "stretch",
    "rsi",
    "macd",
    "adx",
    "efficiency",
    "vol_pct",
    "order",
    "hurst",
    "intermittency",
    "tails",
    "reversion",
    "hour_sin",
    "hour_cos",
)
MIN_TRAIN = 30  # setups with a known outcome before a model is fitted at all
MIN_EVALUATED = 40  # walk-forward predictions before the model may count
PROVEN_T = 2.0  # Diebold-Mariano t against the base rate
RIDGE = 2.0  # L2 penalty on standardised features: strong, the samples are few
REFIT_EVERY = 10
MAX_SETUPS = 400  # the most recent resolved setups: recent behaviour, bounded CPU per hour


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def features(state: TimeframeState, side: int, at: datetime) -> list[float]:
    """The chart at entry, oriented to the trade (+ = with the trade); missing reads are
    neutral."""
    atr = state.atr14 or 0.0
    stretch = 0.0
    if state.close is not None and state.ema20 is not None and atr:
        stretch = (state.close - state.ema20) / atr
    wib = to_wib(at)
    hour = 2 * math.pi * (wib.hour + wib.minute / 60) / 24
    return [
        _clip(side * (state.kalman_t or 0.0) / 5),
        _clip(side * stretch / 3),
        side * ((state.rsi14 if state.rsi14 is not None else 50.0) - 50) / 50,
        _clip(side * (state.macd_hist or 0.0) / atr) if atr else 0.0,
        (state.adx if state.adx is not None else 20.0) / 50,
        state.efficiency if state.efficiency is not None else 0.0,
        state.vol_pct if state.vol_pct is not None else 0.5,
        20 * (1 - state.entropy) if state.entropy is not None else 0.0,
        (state.hurst if state.hurst is not None else 0.5) - 0.5,
        state.mf_width or 0.0,
        _clip((state.tail_alpha or 5.0) / 10, 0.0, 1.0),
        _clip((state.ou_t or 0.0) / 6),
        math.sin(hour),
        math.cos(hour),
    ]


def fit_logistic(x: Array, y: Array, ridge: float = RIDGE) -> JSON:
    """Standardise, then Newton's method on the penalised log-likelihood (intercept free)."""
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale = np.where(scale > 1e-9, scale, 1.0)
    z = (x - mean) / scale
    rate = min(max(float(y.mean()), 1e-3), 1 - 1e-3)
    weights = np.zeros(z.shape[1])
    intercept = math.log(rate / (1 - rate))
    design = np.hstack([z, np.ones((len(z), 1))])
    penalty = np.diag([ridge] * z.shape[1] + [0.0])
    for _ in range(50):
        p = 1 / (1 + np.exp(-(z @ weights + intercept)))
        theta = np.append(weights, intercept)
        gradient = design.T @ (p - y) + penalty @ theta
        hessian = design.T @ (design * (p * (1 - p))[:, None]) + penalty + 1e-9 * np.eye(len(theta))
        step = np.linalg.solve(hessian, gradient)
        weights, intercept = weights - step[:-1], float(intercept - step[-1])
        if float(np.abs(step).max()) < 1e-8:
            break
    return {
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "weights": weights.tolist(),
        "intercept": intercept,
    }


def predict(model: JSON, row: Sequence[float]) -> float:
    z = (np.asarray(row) - np.asarray(model["mean"])) / np.asarray(model["scale"])
    logit = float(z @ np.asarray(model["weights"])) + float(model["intercept"])
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, logit))))  # a live outlier cannot overflow


def evaluate(x: Array, y: Array, closed: Sequence[datetime], entered: Sequence[datetime]) -> JSON:
    """Walk-forward Brier scores of the model and of the base rate on the same setups."""
    model: JSON | None = None
    fitted_on = 0
    model_loss: list[float] = []
    base_loss: list[float] = []
    for k in range(len(y)):
        known = [j for j in range(k) if closed[j] <= entered[k]]
        if len(known) < MIN_TRAIN:
            continue
        if model is None or len(known) - fitted_on >= REFIT_EVERY:
            model, fitted_on = fit_logistic(x[known], y[known]), len(known)
        base = (float(y[known].sum()) + 1) / (len(known) + 2)
        model_loss.append((predict(model, x[k]) - float(y[k])) ** 2)
        base_loss.append((base - float(y[k])) ** 2)
    evaluated = len(model_loss)
    if not evaluated:
        return {"evaluated": 0, "brier": None, "base_brier": None, "skill": None, "t": None}
    gain = np.asarray(base_loss) - np.asarray(model_loss)
    spread = float(gain.std(ddof=1)) if evaluated > 1 else 0.0
    brier, base_brier = float(np.mean(model_loss)), float(np.mean(base_loss))
    return {
        "evaluated": evaluated,
        "brier": round(brier, 4),
        "base_brier": round(base_brier, 4),
        "skill": round(1 - brier / base_brier, 4) if base_brier > 0 else None,
        "t": round(float(gain.mean()) / (spread / math.sqrt(evaluated)), 3) if spread > 0 else None,
    }


def train_meta(tf: Timeframe, bars: Sequence[Candle], setups: Sequence[Setup], window: int) -> JSON:
    """The model for one chart from its replayed setups (signals/setups.py); runs in a worker
    thread. `window`: bars the live chart holds (market.candle_buffer)."""
    resolved = [s for s in setups if s.outcome != "expired"][-MAX_SETUPS:]
    summary: JSON = {"features": list(FEATURES), "samples": len(resolved), "proven": False}
    if len(resolved) < MIN_TRAIN:
        return summary | {"evaluated": 0}
    rows = [
        features(
            timeframe_state(tf, bars[max(0, s.index + 1 - window) : s.index + 1], cache=False),
            s.side,
            s.at,
        )
        for s in resolved
    ]
    x = np.asarray(rows, dtype=np.float64)
    y = np.asarray([1.0 if s.outcome == "tp1" else 0.0 for s in resolved])
    scores = evaluate(x, y, [s.closed_at for s in resolved], [s.at for s in resolved])
    proven = (
        scores["evaluated"] >= MIN_EVALUATED and scores["t"] is not None and scores["t"] >= PROVEN_T
    )
    return (
        summary
        | fit_logistic(x, y)
        | scores
        | {
            "base_rate": round(float(y.mean()), 4),
            "proven": proven,
        }
    )

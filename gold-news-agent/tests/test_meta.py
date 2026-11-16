"""Meta-labeling (signals/meta.py): it proves a real condition walk-forward and does not prove
noise; only a proven model changes the odds (signals/odds.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import numpy as np

from gold_agent.models import Candle, TimeframeState
from gold_agent.signals.meta import FEATURES, evaluate, features, fit_logistic, predict, train_meta
from gold_agent.signals.odds import trade_odds
from gold_agent.signals.setups import replay_setups

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def walk_forward(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    times = [T0 + timedelta(hours=i) for i in range(len(y))]
    closed = [t + timedelta(minutes=30) for t in times]
    return evaluate(x, y, closed, times)


def test_a_real_condition_is_proven_walk_forward_and_noise_is_not() -> None:
    rng = np.random.default_rng(1)
    x = rng.standard_normal((200, len(FEATURES)))
    y = (1.5 * x[:, 0] + rng.standard_normal(200) > 0).astype(float)  # trend_t decides
    real = walk_forward(x, y)
    assert real["evaluated"] == 200 - 30 and real["t"] >= 2 and real["skill"] > 0.05
    model = fit_logistic(x, y)
    assert model["weights"][0] > 0.5 and all(
        abs(w) < model["weights"][0] for w in model["weights"][1:]
    )
    proven_noise = 0
    for seed in range(10):
        noise = np.random.default_rng(100 + seed)
        scores = walk_forward(
            noise.standard_normal((200, len(FEATURES))), noise.integers(0, 2, 200).astype(float)
        )
        proven_noise += scores["t"] is not None and scores["t"] >= 2
    assert proven_noise <= 1  # beating the base rate on noise is rare


def test_features_are_oriented_to_the_trade() -> None:
    state = TimeframeState(tf="M15", close=2660, ema20=2650, atr14=5, kalman_t=3.0, rsi14=70)
    buy = dict(zip(FEATURES, features(state, 1, T0), strict=True))
    sell = dict(zip(FEATURES, features(state, -1, T0), strict=True))
    assert buy["trend_t"] > 0 > sell["trend_t"] and buy["stretch"] > 0 > sell["stretch"]
    assert buy["rsi"] == -sell["rsi"] and buy["adx"] == sell["adx"]
    empty = features(TimeframeState(tf="M5"), 1, T0)
    assert len(empty) == len(FEATURES) and all(np.isfinite(empty))


def bars(closes: np.ndarray) -> list[Candle]:
    return [
        Candle(
            source="mt5",
            tf="M15",
            open_time=T0 + timedelta(minutes=15 * i),
            close_time=T0 + timedelta(minutes=15 * (i + 1)),
            open=float(c),
            high=float(c) + 2.0,
            low=float(c) - 2.0,
            close=float(c),
            complete=True,
        )
        for i, c in enumerate(closes)
    ]


def test_the_model_trains_on_replayed_setups_from_the_live_chart_reads() -> None:
    rng = np.random.default_rng(2)
    path = bars(2650 + np.cumsum(2.0 * rng.standard_normal(1400)))
    scores = [(bar.close_time, 0.5 if i % 2 else -0.5) for i, bar in enumerate(path)]
    setups = replay_setups(
        bars=path,
        scores=scores,
        held=[],
        threshold=0.35,
        sl_atr=1.2,
        tp1_r=1.0,
        validity_bars=6,
        now=path[-1].close_time,
        start=path[300].close_time,
    )
    resolved = [s for s in setups if s.outcome != "expired"]
    assert len(resolved) >= 40 and all(a.closed_at <= b.at for a, b in pairwise(setups))
    model = train_meta("M15", path, setups, window=600)
    assert model["samples"] == len(resolved) and len(model["weights"]) == len(FEATURES)
    assert not model["proven"]  # a random walk teaches nothing
    assert train_meta("M15", path, setups[:10], window=600) == {
        "features": list(FEATURES),
        "samples": len([s for s in setups[:10] if s.outcome != "expired"]),
        "proven": False,
        "evaluated": 0,
    }


STATE = TimeframeState(tf="M15", close=2650.0, ema20=2648.0, atr14=5.0, sigma=3.0, kalman_t=3.0)
PLAN: dict[str, Any] = {
    "side": "BUY",
    "entry": 2650.0,
    "stop_loss": 2644.0,
    "tp1": 2656.0,
    "tp2": 2662.0,
    "spread": 0.3,
    "state": STATE,
    "tf": "M15",
    "validity_bars": 6,
    "min_calls": 40,
    "tp1_fraction": 0.5,
    "at": T0,
}


def test_only_a_proven_model_moves_the_odds() -> None:
    rng = np.random.default_rng(3)
    x = rng.standard_normal((200, len(FEATURES)))
    y = (1.5 * x[:, 0] + rng.standard_normal(200) > 0).astype(float)
    model = {"features": list(FEATURES), "samples": 200, **fit_logistic(x, y), "t": 3.1}
    model |= {"skill": 0.2, "evaluated": 170}
    record = {"tp1": 50, "sl": 50, "expired": 10}
    plain = trade_odds(**PLAN, accuracy={"setups": record})
    learned = trade_odds(**PLAN, accuracy={"setups": record, "meta": model | {"proven": True}})
    shown = trade_odds(**PLAN, accuracy={"setups": record, "meta": model | {"proven": False}})
    assert plain is not None and learned is not None and shown is not None
    p = predict(model, features(STATE, 1, T0))
    assert p > 0.6 and learned.meta_tp1 is not None and abs(learned.meta_tp1 - p) < 1e-3
    assert learned.tp1 > plain.tp1 + 0.05  # the record split by this chart's state
    assert shown.tp1 == plain.tp1 and shown.meta_note is not None
    assert "belum terbukti, tidak dipakai" in shown.meta_note
    assert learned.meta_note is not None and "terbukti, dipakai" in learned.meta_note
    assert learned.setups == plain.setups == 110 and learned.setups_tp1 == 50

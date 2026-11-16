"""The statistics behind the chart reads (market/quant.py, MACD/ADX): each one recovers what a
known series was built with, and the first-passage odds agree with their closed form."""

from __future__ import annotations

import math

import numpy as np
import pytest

from gold_agent.market import indicators as ind
from gold_agent.market import quant


def garch_returns(n: int, alpha: float, beta: float, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    omega = 1e-6 * (1 - alpha - beta)
    var, out = 1e-6, []
    for _ in range(n):
        r = math.sqrt(var) * rng.standard_normal()
        out.append(r)
        var = omega + alpha * r * r + beta * var
    return np.array(out)


def test_garch_finds_clustered_volatility_and_mean_reverts_over_the_horizon() -> None:
    fitted = quant.garch11(garch_returns(1500, 0.10, 0.85))
    assert fitted is not None and 0.85 <= fitted.persistence < 0.995
    assert fitted.alpha >= 0.05  # shocks matter, unlike in plain noise
    one = math.sqrt(fitted.next_var)
    assert fitted.horizon_sigma(4) == pytest.approx(math.sqrt(sum(fitted.path_vars(4))), rel=1e-9)
    assert fitted.horizon_sigma(4) > one
    assert quant.garch11(np.zeros(50)) is None  # too short to fit


def test_hurst_and_efficiency_tell_trends_from_noise_and_chop() -> None:
    rng = np.random.default_rng(5)
    walk = list(100 * np.exp(np.cumsum(rng.standard_normal(2000) * 0.001)))
    steps, trending = [0.0], []
    for shock in rng.standard_normal(2000):
        steps.append(0.85 * steps[-1] + shock)  # positively autocorrelated moves
    trending = list(100 * np.exp(np.cumsum(np.array(steps) * 0.0005)))
    chop = [100 + (0.5 if i % 2 else -0.5) + 0.01 * rng.standard_normal() for i in range(2000)]
    assert quant.hurst(walk) == pytest.approx(0.5, abs=0.05)
    assert (quant.hurst(trending) or 0) > 0.6
    choppy = quant.hurst(chop)
    assert choppy is not None and choppy < 0.2  # strongly mean-reverting
    assert quant.efficiency_ratio([100 + i for i in range(30)]) == pytest.approx(1.0)
    ratio = quant.efficiency_ratio(chop)
    assert ratio is not None and ratio < 0.1


def test_kalman_trend_recovers_the_slope_and_is_calibrated_on_a_random_walk() -> None:
    rng = np.random.default_rng(0)

    def t_of_a_random_walk() -> float:
        trend = quant.kalman_trend(list(2650 + np.cumsum(rng.standard_normal(600))))
        assert trend is not None
        return trend.t

    false_alarms = sum(abs(t_of_a_random_walk()) >= 2 for _ in range(400))
    assert 0.02 <= false_alarms / 400 <= 0.08  # |t| >= 2 about 5% of the time without a trend

    rng = np.random.default_rng(9)
    rising = [100 + 0.2 * i + rng.standard_normal() * 0.3 for i in range(400)]
    flat = [100 + rng.standard_normal() * 0.3 for _ in range(400)]
    trend = quant.kalman_trend(rising)
    assert trend is not None and trend.slope == pytest.approx(0.2, abs=0.05) and trend.t > 3
    calm = quant.kalman_trend(flat)
    assert calm is not None and abs(calm.t) < 2
    _, slope, se = quant.kalman_series(rising)
    assert math.isnan(slope[10]) and not math.isnan(slope[-1]) and se[-1] > 0


def test_macd_and_adx_read_a_rally() -> None:
    closes = [100 + 0.3 * i + math.sin(i / 3) for i in range(200)]
    highs, lows = [c + 0.5 for c in closes], [c - 0.5 for c in closes]
    line, _, hist = ind.macd(closes)
    strength, plus_di, minus_di = ind.adx(highs, lows, closes)
    assert (ind.last(line) or 0) > 0 and ind.last(hist) is not None
    assert (ind.last(strength) or 0) > 25 and (ind.last(plus_di) or 0) > (ind.last(minus_di) or 0)
    assert math.isnan(float(strength[10]))  # warm-up
    assert quant.classify(0.5, 0.6, 30, 0.5) == "trending"
    assert quant.classify(0.1, 0.4, 15, 0.5) == "ranging"
    assert quant.classify(0.5, 0.6, 30, 0.95) == "volatile"


def test_first_passage_odds_match_their_closed_form() -> None:
    assert quant.first_passage(1, 1, 0, 1) == pytest.approx(0.5)
    assert quant.first_passage(2, 1, 0, 1) == pytest.approx(1 / 3)  # far target, near stop
    up = quant.first_passage(1, 1, 0.1, 1)
    assert up == pytest.approx(math.expm1(-0.2) / math.expm1(-0.4)) and up > 0.5
    assert quant.first_passage(1, 1, -0.1, 1) == pytest.approx(1 - up)
    long_run = quant.simulate_passage(1.0, 1.0, 0.1, [1.0] * 40, paths=6000)
    assert long_run.target == pytest.approx(up, abs=0.03) and long_run.neither < 0.01
    short = quant.simulate_passage(3.0, 3.0, 0.0, [1.0] * 2)
    assert short.neither > 0.8  # three sigma away in two bars: usually neither
    assert quant.simulate_passage(1.0, 1.0, 0.0, [1.0] * 6) == quant.simulate_passage(
        1.0, 1.0, 0.0, [1.0] * 6
    )  # same inputs, same answer (replays)


def test_a_proven_hit_rate_becomes_a_drift_in_the_trade_direction() -> None:
    assert quant.drift_from_hit_rate(0.5, 2.0) == pytest.approx(0.0, abs=1e-9)
    drift = quant.drift_from_hit_rate(0.55, 2.0)
    assert drift == pytest.approx(2.0 * 0.1257, abs=1e-3)


def test_regime_labels_rarely_fire_on_a_random_walk_and_find_real_ones() -> None:
    rng = np.random.default_rng(2)

    def label(closes: list[float]) -> str:
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        strength = ind.last(ind.adx(highs, lows, closes)[0])
        return quant.classify(quant.efficiency_ratio(closes), quant.hurst(closes), strength, 0.5)

    def reverting() -> list[float]:
        x = [0.0]
        for shock in rng.standard_normal(599):
            x.append(0.85 * x[-1] + shock)
        return list(2650 + np.array(x))

    walks = [label(list(2650 + np.cumsum(rng.standard_normal(600)))) for _ in range(200)]
    assert walks.count("trending") / 200 <= 0.1 and walks.count("ranging") / 200 <= 0.12
    assert [label(reverting()) for _ in range(50)].count("ranging") >= 40
    trends = [label(list(2650 + np.cumsum(rng.standard_normal(600) + 0.5))) for _ in range(50)]
    assert trends.count("trending") >= 20


def test_fat_tails_jump_to_a_distant_target_more_often_and_invent_no_direction() -> None:
    normal = quant.simulate_passage(3.0, 3.0, 0.0, [1.0])
    fat = quant.simulate_passage(3.0, 3.0, 0.0, [1.0], nu=3.0)
    assert fat.target > 2.5 * normal.target  # a 3-sigma bar is rare on a normal curve only
    assert fat.target == pytest.approx(fat.stop, abs=0.003)  # no drift: still symmetric
    near = quant.simulate_passage(1.0, 1.0, 0.0, [1.0] * 6, nu=4.0)
    assert near.target == pytest.approx(near.stop, abs=0.01)


def test_a_real_mean_reversion_pulls_the_odds_towards_the_mean() -> None:
    plain = quant.simulate_passage(1.0, 1.0, 0.0, [1.0] * 6)
    towards = quant.simulate_passage(1.0, 1.0, 0.0, [1.0] * 6, revert=(0.2, 3.0))
    away = quant.simulate_passage(1.0, 1.0, 0.0, [1.0] * 6, revert=(0.2, -3.0))
    assert towards.target > plain.target + 0.1 and away.target < plain.target - 0.1
    assert towards == quant.simulate_passage(1.0, 1.0, 0.0, [1.0] * 6, revert=(0.2, 3.0))

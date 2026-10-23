"""The econophysics reads (market/econophysics.py): each estimator recovers what a known series
was built with, and each reading that could fire on noise stays quiet on noise."""

from __future__ import annotations

import math

import numpy as np
import pytest

from gold_agent.market import econophysics as ep


def garch(rng: np.random.Generator, n: int, alpha: float = 0.1, beta: float = 0.88) -> np.ndarray:
    var, out = 1.0, []
    for _ in range(n):
        r = math.sqrt(var) * rng.standard_normal()
        out.append(r)
        var = (1 - alpha - beta) + alpha * r * r + beta * var
    return np.array(out)


def ar1(rng: np.random.Generator, phi: float, n: int = 600) -> np.ndarray:
    x = np.zeros(n)
    shocks = rng.standard_normal(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + shocks[i]
    return x


def test_power_law_tails_and_student_t_degrees_are_recovered() -> None:
    rng = np.random.default_rng(1)
    t3 = [ep.hill_alpha(rng.standard_t(3, 600)) or 0 for _ in range(40)]
    normal = [ep.hill_alpha(rng.standard_normal(600)) or 0 for _ in range(40)]
    assert 2.2 <= float(np.median(t3)) <= 3.4  # the inverse cubic law's alpha ~ 3
    assert float(np.median(normal)) > ep.ALPHA_FAT
    assert ep.student_nu(rng.standard_t(3, 2000)) in (2.5, 3.0, 3.5, 4.0)
    assert ep.student_nu(rng.standard_normal(2000)) is None  # a normal curve: no fat tails
    assert ep.hill_alpha(rng.standard_normal(100)) is None  # too few for a tail


def test_permutation_entropy_is_one_for_noise_and_falls_with_structure() -> None:
    rng = np.random.default_rng(2)
    noise = [ep.permutation_entropy(rng.standard_normal(600)) or 0 for _ in range(200)]
    clustered = [ep.permutation_entropy(garch(rng, 600)) or 0 for _ in range(40)]
    persistent = [ep.permutation_entropy(ar1(rng, 0.3)) or 1 for _ in range(60)]
    assert float(np.mean(np.array(noise) < ep.PE_STRUCTURED)) <= 0.03
    assert float(np.mean(np.array(clustered) < ep.PE_STRUCTURED)) <= 0.1  # volatility is not order
    assert float(np.mean(np.array(persistent) < ep.PE_STRUCTURED)) >= 0.5
    assert ep.permutation_entropy(np.arange(600, dtype=float)) == pytest.approx(0.0)


def test_mfdfa_reads_the_hurst_exponent_and_intermittency() -> None:
    rng = np.random.default_rng(3)
    noise = [ep.mfdfa(rng.standard_normal(600)) for _ in range(60)]
    assert all(read is not None for read in noise)
    h2 = [read.h2 for read in noise if read]
    widths = [read.width for read in noise if read]
    assert float(np.median(h2)) == pytest.approx(0.5, abs=0.05)
    assert float(np.mean(np.array(widths) >= ep.MF_MODERATE)) <= 0.1

    def cascade() -> np.ndarray:  # a binomial multiplicative cascade: textbook multifractal
        weight = np.ones(1)
        for _ in range(10):
            split = np.where(rng.random(len(weight)) < 0.5, 0.25, 0.75)
            weight = np.repeat(weight, 2) * np.ravel(np.column_stack([split, 1 - split]))
        return (rng.standard_normal(len(weight)) * np.sqrt(weight * len(weight)))[:600]

    turbulent = [ep.mfdfa(cascade()) for _ in range(30)]
    assert float(np.median([read.width for read in turbulent if read])) > ep.MF_MODERATE
    trending = [ep.mfdfa(ar1(rng, 0.5)) for _ in range(30)]
    assert float(np.median([read.h2 for read in trending if read])) > 0.6  # persistent moves
    assert ep.mfdfa(rng.standard_normal(100)) is None


def test_mean_reversion_is_calibrated_on_random_walks_and_found_when_real() -> None:
    rng = np.random.default_rng(4)
    walks = [ep.ou_fit(2650 + np.cumsum(rng.standard_normal(600))) for _ in range(400)]
    false_alarms = sum(1 for fit in walks if fit and fit.significant) / 400
    assert 0.02 <= false_alarms <= 0.08  # the Dickey-Fuller 5% level
    pulled = [ep.ou_fit(2650 + ar1(rng, math.exp(-0.05))) for _ in range(60)]
    assert all(fit is not None and fit.significant for fit in pulled)
    half_lives = [fit.half_life for fit in pulled if fit and fit.half_life]
    assert 8 <= float(np.median(half_lives)) <= 20  # ln 2 / 0.05 = 13.9 bars
    fit = pulled[0]
    assert fit is not None and fit.mean == pytest.approx(2650, abs=10)


def simulate_hawkes(
    rng: np.random.Generator, mu: float, branching: float, decay: float, horizon: float
) -> list[float]:
    """Ogata's thinning."""
    t, events, memory = 0.0, [], 0.0
    while True:
        bound = mu + branching * decay * memory
        wait = rng.exponential(1 / bound)
        memory *= math.exp(-decay * wait)
        t += wait
        if t >= horizon:
            return events
        if rng.random() <= (mu + branching * decay * memory) / bound:
            events.append(t)
            memory += 1.0


def test_hawkes_recovers_self_excitation_and_does_not_invent_it() -> None:
    rng = np.random.default_rng(5)
    exciting = [
        ep.hawkes_fit(simulate_hawkes(rng, 0.004, 0.6, 0.05, 7200), 7200) for _ in range(20)
    ]
    assert all(fit is not None and fit.clustered for fit in exciting)
    assert 0.4 <= float(np.median([fit.branching for fit in exciting if fit])) <= 0.75
    memoryless = [
        ep.hawkes_fit(np.sort(rng.uniform(0, 7200, rng.poisson(72))).tolist(), 7200)
        for _ in range(30)
    ]
    assert sum(1 for fit in memoryless if fit and fit.clustered) <= 1
    assert float(np.median([fit.branching for fit in memoryless if fit])) < 0.2
    assert ep.hawkes_fit([1.0, 2.0, 3.0], 10) is None  # too few events


def test_aftershocks_read_a_clustered_minute_series() -> None:
    rng = np.random.default_rng(6)
    minutes = np.arange(7200, dtype=float)
    clustered = ep.aftershocks(minutes, 2650 * np.exp(np.cumsum(3e-4 * garch(rng, 7200))))
    calm = ep.aftershocks(minutes, 2650 * np.exp(np.cumsum(3e-4 * rng.standard_normal(7200))))
    assert clustered is not None and clustered.fit.clustered and clustered.fit.branching > 0.3
    assert calm is not None and not calm.fit.clustered
    assert 0 <= clustered.next_hour <= 1 and clustered.threshold_pct > 0
    # A shock in the last minutes: the intensity now is far above its average.
    shocked = np.exp(np.cumsum(3e-4 * rng.standard_normal(7200)))
    for back in (30, 20, 12, 6, 3):
        shocked[-back:] *= 1.004
    fresh = ep.aftershocks(minutes, 2650 * shocked)
    assert fresh is not None and fresh.intensity_ratio > ep.AFTERSHOCK_HIGH
    # A weekend gap is not a move.
    gapped = ep.aftershocks(np.concatenate([minutes[:3600], minutes[3600:] + 2880]), 2650 * shocked)
    assert gapped is not None and gapped.events == fresh.events


def test_transfer_entropy_finds_a_leader_and_ignores_noise_and_spillover() -> None:
    rng = np.random.default_rng(7)

    def lagged(coupling: float) -> tuple[np.ndarray, np.ndarray]:
        x = rng.standard_normal(2000)
        y = np.concatenate([[0.0], coupling * x[:-1] + rng.standard_normal(1999)])
        return x, y

    leaders = [ep.information_flow(*lagged(0.15)) for _ in range(30)]
    assert sum(1 for flow in leaders if flow and flow.leads) >= 22
    assert all(flow is not None and flow.corr > 0 for flow in leaders)
    inverse = ep.information_flow(*lagged(-0.3))
    assert inverse is not None and inverse.leads and inverse.corr < 0  # leads, the other way
    noise = [
        ep.information_flow(rng.standard_normal(2000), rng.standard_normal(2000)) for _ in range(40)
    ]
    assert not any(flow and flow.leads for flow in noise)
    spill = []
    for _ in range(40):  # gold's next move is wilder after a big source move, in no direction
        x = rng.standard_normal(2000)
        y = np.concatenate([[0.0], rng.standard_normal(1999) * (0.5 + np.abs(x[:-1]))])
        spill.append(ep.information_flow(x, y))
    assert not any(flow and flow.leads for flow in spill)
    # Gold leading the source is not the source leading gold.
    x, y = lagged(0.3)
    backwards = ep.information_flow(y, x)
    assert backwards is not None and not backwards.leads


def test_random_matrix_theory_separates_a_common_factor_from_noise() -> None:
    rng = np.random.default_rng(8)
    noise = [ep.market_mode(rng.standard_normal((1400, 8))) for _ in range(60)]
    assert sum(1 for mode in noise if mode and mode.factors > 0) <= 2
    factor = rng.standard_normal((1400, 1))
    loads = np.array([[0.7, 0.6, 0.5, 0.4, 0.6, 0.5, 0.3, 0.6]])
    mode = ep.market_mode(factor @ loads + rng.standard_normal((1400, 8)))
    assert mode is not None and mode.factors >= 1 and mode.share > 0.25
    assert mode.gold_loading > 0.4  # gold (column 0) moves with the common factor
    assert ep.market_mode(rng.standard_normal((20, 8))) is None  # too short for 8 markets


def test_lppls_flags_a_bubble_and_rarely_a_random_walk() -> None:
    rng = np.random.default_rng(9)
    days = np.arange(300, dtype=float)
    critical = 299 + 15

    def bubble() -> np.ndarray:
        f = (critical - days) ** 0.5
        log_price = -0.03 * f + 0.003 * f * np.cos(8 * np.log(critical - days))
        return 4000 * np.exp(log_price + np.cumsum(0.005 * rng.standard_normal(300)))

    bubbles = [ep.bubble_confidence(bubble()) for _ in range(6)]
    assert float(np.median([read.bubble for read in bubbles if read])) >= ep.BUBBLE_ALERT
    timings = [read.days_to_tc for read in bubbles if read and read.days_to_tc]
    assert 5 <= float(np.median(timings)) <= 30  # the critical time was 15 days ahead
    walks = [
        ep.bubble_confidence(4000 * np.exp(np.cumsum(0.01 * rng.standard_normal(300))))
        for _ in range(6)
    ]
    assert sum(1 for read in walks if read and read.bubble >= ep.BUBBLE_ALERT) <= 1
    falling = ep.bubble_confidence(4000 * 4000 / bubble())
    assert falling is not None and falling.bubble < ep.BUBBLE_ALERT
    short = ep.bubble_confidence([4000.0] * 40)
    assert short is None  # under the shortest window

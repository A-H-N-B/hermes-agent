"""Small statistics helpers shared by metrics, reports and evaluation."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0, 1]. Raises on empty input."""
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q
    low = math.floor(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion; (0, 1) when there is no data."""
    if total <= 0:
        return (0.0, 1.0)
    phat = successes / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def bootstrap_mean_ci(
    values: Sequence[float], *, iterations: int = 2000, alpha: float = 0.05, seed: int = 7
) -> tuple[float, float] | None:
    """Percentile bootstrap CI for the mean; None with fewer than 2 values."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)  # noqa: S311 - resampling, not cryptography
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(iterations))
    return (percentile(means, alpha / 2), percentile(means, 1 - alpha / 2))


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """Largest peak-to-trough drop of a cumulative curve (same units as the curve)."""
    peak = -math.inf
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst

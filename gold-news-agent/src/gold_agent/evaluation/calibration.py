"""Composite-weight calibration (C3.2), as a proposal a human applies, never silently.

Each component's information coefficient (Spearman rank correlation with the forward move) is
measured on the older part of the engine-state history; weights follow the positive ICs. The
proposal is accepted only if the re-weighted composite predicts better than the current one on
the newer, untouched part (time-ordered holdout). Nothing runs before CALIBRATION_SAMPLES closed
paper signals.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from gold_agent.config import Weights
from gold_agent.evaluation.performance import CALIBRATION_SAMPLES

COMPONENTS = ("N", "M", "T", "P")
FIELD = {"N": "news", "M": "macro", "T": "technical", "P": "price_confirmation"}


@dataclass(frozen=True)
class StateSample:
    ts: datetime
    components: dict[str, float]  # N, M, T, P at that moment
    forward: float  # gold move over the horizon that followed ($)


@dataclass(frozen=True)
class WeightProposal:
    ready: bool
    reason: str  # Bahasa Indonesia
    current: dict[str, float]
    proposed: dict[str, float] | None = None
    ic_train: dict[str, float] = field(default_factory=lambda: dict[str, float]())
    holdout_ic_current: float | None = None
    holdout_ic_proposed: float | None = None
    train_samples: int = 0
    holdout_samples: int = 0

    def yaml_snippet(self) -> str:
        weights = self.proposed or self.current
        lines = ["signal_engine:", "  weights:"]
        lines += [f"    {FIELD[key]}: {weights[key]:.2f}" for key in COMPONENTS]
        return "\n".join(lines)


def average_ranks(values: Sequence[float]) -> np.ndarray:
    """Ranks with ties sharing their average rank (components are often exactly 0)."""
    data = np.asarray(values, dtype=float)
    order = np.argsort(data, kind="mergesort")
    ranks = np.empty(len(data), dtype=float)
    ordered = data[order]
    start = 0
    while start < len(data):
        end = start
        while end + 1 < len(data) and ordered[end + 1] == ordered[start]:
            end += 1
        ranks[order[start : end + 1]] = (start + end) / 2
        start = end + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    rx, ry = average_ranks(xs), average_ranks(ys)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _composite_ic(samples: Sequence[StateSample], weights: dict[str, float]) -> float | None:
    scores = [sum(weights[k] * s.components[k] for k in COMPONENTS) for s in samples]
    return spearman(scores, [s.forward for s in samples])


def propose_weights(
    samples: Sequence[StateSample],
    current: Weights,
    *,
    closed_signals: int,
    holdout_fraction: float = 0.3,
    min_samples: int = 200,
) -> WeightProposal:
    now_weights = {k: float(getattr(current, FIELD[k])) for k in COMPONENTS}
    if closed_signals < CALIBRATION_SAMPLES:
        return WeightProposal(
            ready=False,
            reason=(
                f"sampel belum cukup: {closed_signals} sinyal tertutup, kalibrasi bobot butuh "
                f"≥ {CALIBRATION_SAMPLES}"
            ),
            current=now_weights,
        )
    ordered = sorted(samples, key=lambda s: s.ts)
    if len(ordered) < min_samples:
        return WeightProposal(
            ready=False,
            reason=f"riwayat status sinyal terlalu pendek ({len(ordered)} < {min_samples})",
            current=now_weights,
        )
    cut = int(len(ordered) * (1 - holdout_fraction))
    train, holdout = ordered[:cut], ordered[cut:]
    ic = {
        k: spearman([s.components[k] for s in train], [s.forward for s in train]) or 0.0
        for k in COMPONENTS
    }
    positive = {k: max(v, 0.0) for k, v in ic.items()}
    total = sum(positive.values())
    if total <= 0:
        return WeightProposal(
            ready=False,
            reason="tidak ada komponen yang searah dengan gerak harga di data latih",
            current=now_weights,
            ic_train={k: round(v, 3) for k, v in ic.items()},
            train_samples=len(train),
            holdout_samples=len(holdout),
        )
    proposed = {k: round(v / total, 3) for k, v in positive.items()}
    current_ic = _composite_ic(holdout, now_weights)
    proposed_ic = _composite_ic(holdout, proposed)
    better = proposed_ic is not None and (current_ic is None or proposed_ic > current_ic)
    return WeightProposal(
        ready=better,
        reason=(
            "bobot usulan lebih baik di data uji (holdout)"
            if better
            else "bobot usulan tidak lebih baik di data uji: pertahankan bobot sekarang"
        ),
        current=now_weights,
        proposed=proposed,
        ic_train={k: round(v, 3) for k, v in ic.items()},
        holdout_ic_current=None if current_ic is None else round(current_ic, 3),
        holdout_ic_proposed=None if proposed_ic is None else round(proposed_ic, 3),
        train_samples=len(train),
        holdout_samples=len(holdout),
    )

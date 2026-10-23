"""Learned source reliability: how often a source's stories moved gold the predicted way.

Shrunk towards the configured prior (`prior_weight` pseudo-observations), so one lucky week does
not make a source trusted; nothing is learned below `min_samples` directional stories."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from gold_agent.evaluation.event_study import EventMove


def learn_reliability(
    moves: Sequence[EventMove],
    priors: Mapping[str, float],
    *,
    prior_weight: float,
    min_samples: int,
    horizon: int = 15,
    min_impact: int = 3,
) -> dict[str, tuple[float, int]]:
    """source_id -> (learned reliability, samples) for sources with enough evidence."""
    tallies: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [hits, n]
    for move in moves:
        if move.impact < min_impact:
            continue
        signed = move.signed(horizon)
        if signed is None or signed == 0:
            continue
        tally = tallies[move.source_id]
        tally[0] += signed > 0
        tally[1] += 1
    learned: dict[str, tuple[float, int]] = {}
    for source_id, (hits, n) in tallies.items():
        if n < min_samples or source_id not in priors:
            continue
        prior = priors[source_id]
        value = (prior * prior_weight + hits) / (prior_weight + n)
        learned[source_id] = (round(value, 4), n)
    return learned

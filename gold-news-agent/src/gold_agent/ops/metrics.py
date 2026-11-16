"""Latency metrics (section 4): p50/p95 per source and per pipeline stage."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel

from gold_agent.models import LatencyStage
from gold_agent.stats import percentile


class LatencySummary(BaseModel):
    source_id: str
    stage: str
    samples: int
    p50_seconds: float
    p95_seconds: float
    max_seconds: float


def summarize(stages: Iterable[LatencyStage]) -> list[LatencySummary]:
    """Group by (source, stage). For `published` the value is the source's own delay:
    first_seen - published (positive = we saw it that many seconds after publication)."""
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for stage in stages:
        if stage.seconds_from_first_seen is None or stage.stage == "first_seen":
            continue
        value = stage.seconds_from_first_seen
        if stage.stage == "published":
            value = -value
        groups[(stage.source_id, stage.stage)].append(value)
    return [
        LatencySummary(
            source_id=source_id,
            stage=stage,
            samples=len(values),
            p50_seconds=round(percentile(values, 0.5), 3),
            p95_seconds=round(percentile(values, 0.95), 3),
            max_seconds=round(max(values), 3),
        )
        for (source_id, stage), values in sorted(groups.items())
    ]

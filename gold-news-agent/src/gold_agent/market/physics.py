"""Market-wide econophysics, rebuilt every minute by the market service (in a thread).

From copies of the minute series the service keeps (gold, and the related markets read from the
broker, market/cross_assets.py) and the daily closes:

- aftershocks: a Hawkes process of gold's big 1-minute moves (econophysics.aftershocks);
- leaders: the transfer entropy of each related market's 1-minute move to gold's next one,
  with the sign of the lead and what its latest move implies for gold;
- the common factor: random matrix theory on 5-minute returns of gold and the related markets;
- bubble risk: the LPPLS confidence on daily closes, recomputed once per new daily bar.

The inputs are plain lists, so the event loop can hand them over and keep updating its series.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import reduce

import numpy as np
from numpy.typing import NDArray

from gold_agent.market import econophysics as ep
from gold_agent.models import (
    Aftershock,
    BubbleRisk,
    CommonFactor,
    Leader,
    MarketPhysics,
)

Points = Sequence[tuple[datetime, float]]
Returns = tuple[NDArray[np.int64], NDArray[np.float64]]
FACTOR_STEP = timedelta(minutes=5)
FACTOR_COVERAGE = 0.8  # a market joins the factor analysis when it trades 80% of gold's buckets


@dataclass(frozen=True)
class PhysicsInputs:
    now: datetime
    gold: Points  # last value per minute
    related: Mapping[str, Points]  # logical market key -> last value per minute
    labels: Mapping[str, str]
    daily: Sequence[float]  # closes of complete daily bars, oldest first
    daily_as_of: str | None  # the last of them, as a date


def _log_series(points: Points) -> Returns:
    """(epoch minute, log value) of the positive points, oldest first."""
    minutes = np.fromiter((int(ts.timestamp() // 60) for ts, _ in points), np.int64, len(points))
    values = np.fromiter((value for _, value in points), np.float64, len(points))
    keep = values > 0
    return minutes[keep], np.log(values[keep])


def minute_returns(points: Points) -> Returns:
    """(epoch minute, log return ending there) for each minute whose previous point is exactly one
    minute earlier."""
    minutes, logs = _log_series(points)
    whole = np.diff(minutes) == 1
    return minutes[1:][whole], np.diff(logs)[whole]


def bucket_returns(points: Points, step: timedelta = FACTOR_STEP) -> Returns:
    """(bucket number, log return) of the last value per `step` bucket against the bucket
    before it."""
    minutes, logs = _log_series(points)
    if not len(minutes):
        return minutes, logs
    ids = minutes // int(step.total_seconds() // 60)
    last = np.append(np.flatnonzero(np.diff(ids)), len(ids) - 1)
    ids, logs = ids[last], logs[last]
    adjacent = np.diff(ids) == 1
    return ids[1:][adjacent], np.diff(logs)[adjacent]


def aftershock(gold: Points) -> Aftershock | None:
    if len(gold) < 1000:
        return None
    origin = gold[0][0]
    read = ep.aftershocks(
        [(ts - origin).total_seconds() / 60 for ts, _ in gold], [value for _, value in gold]
    )
    if read is None:
        return None
    return Aftershock(
        branching=round(read.fit.branching, 3),
        clustered=read.fit.clustered,
        intensity_ratio=round(read.intensity_ratio, 3),
        next_hour=round(read.next_hour, 3),
        half_life_minutes=round(math.log(2) / read.fit.decay, 1),
        events=read.events,
        minutes=read.minutes,
        threshold_pct=round(read.threshold_pct, 4),
    )


def leaders(
    gold: Points, related: Mapping[str, Points], labels: Mapping[str, str], now: datetime
) -> list[Leader]:
    """Each related market's information about gold's next minute, strongest first."""
    target_minutes, target = minute_returns(gold)
    out: list[Leader] = []
    for key, points in related.items():
        source_minutes, source = minute_returns(points)
        _, in_source, in_target = np.intersect1d(
            source_minutes, target_minutes, assume_unique=True, return_indices=True
        )
        flow = ep.information_flow(source[in_source], target[in_target])
        if flow is None:
            continue
        fresh = bool(len(source)) and now.timestamp() - 60 * int(source_minutes[-1]) <= 120
        move = float(source[-1]) if fresh else None
        implied = 0
        if flow.leads and move:
            implied = 1 if move * flow.corr > 0 else -1
        out.append(
            Leader(
                key=key,
                label=labels.get(key, key),
                bits=round(flow.bits, 5),
                reverse_bits=round(flow.reverse_bits, 5),
                z=round(flow.z, 2),
                corr=round(flow.corr, 3),
                leads=flow.leads,
                last_move_pct=round(100 * (math.exp(move) - 1), 4) if move is not None else None,
                implied=implied,
            )
        )
    return sorted(out, key=lambda leader: (not leader.leads, -leader.z))


def common_factor(gold: Points, related: Mapping[str, Points]) -> CommonFactor | None:
    base = bucket_returns(gold)
    base_ids = base[0]
    if len(base_ids) < 100:
        return None
    columns = [base]
    for points in related.values():
        ids, returns = bucket_returns(points)
        if len(np.intersect1d(ids, base_ids)) >= FACTOR_COVERAGE * len(base_ids):
            columns.append((ids, returns))
    if len(columns) < 3:
        return None
    times = reduce(np.intersect1d, (ids for ids, _ in columns))
    matrix = np.column_stack([returns[np.searchsorted(ids, times)] for ids, returns in columns])
    mode = ep.market_mode(matrix) if len(times) else None
    if mode is None:
        return None
    return CommonFactor(
        factors=mode.factors,
        share=round(mode.share, 3),
        gold_loading=round(mode.gold_loading, 3),
        markets=mode.markets,
        observations=mode.observations,
    )


def bubble_risk(daily: Sequence[float], as_of: str | None) -> BubbleRisk | None:
    read = ep.bubble_confidence(daily) if len(daily) >= ep.LPPLS_WINDOWS[0] else None
    if read is None:
        return None
    return BubbleRisk(
        bubble=round(read.bubble, 3),
        anti_bubble=round(read.anti_bubble, 3),
        days_to_tc=round(read.days_to_tc, 1) if read.days_to_tc is not None else None,
        windows=read.windows,
        as_of=as_of,
    )


def build_physics(inputs: PhysicsInputs, previous: MarketPhysics | None = None) -> MarketPhysics:
    """Everything market-wide; the bubble read is reused until a new daily bar completes."""
    bubble = previous.bubble if previous else None
    if bubble is None or bubble.as_of != inputs.daily_as_of:
        bubble = bubble_risk(inputs.daily, inputs.daily_as_of)
    return MarketPhysics(
        built_at=inputs.now,
        aftershock=aftershock(inputs.gold),
        leaders=leaders(inputs.gold, inputs.related, inputs.labels, inputs.now),
        common=common_factor(inputs.gold, inputs.related),
        bubble=bubble,
    )

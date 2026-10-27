"""Momentum impulses: the first seconds of a big move, found the moment they start.

A big move in gold begins as a burst: price covers in a few minutes what it normally covers in
an hour and leaves its recent range. Two things are computed from the complete minute bars:

* how unusual a move is: the change over the last 1, 3, 5 and 10 minutes divided by the
  per-minute volatility times sqrt(minutes) (a z-score; on a random walk |z| >= 3 is rare);
* whether it broke out: above the highest high (below the lowest low) of the last
  `range_minutes` minutes.

Both at once is an impulse. The price at which both become true (the trigger) is fixed until the
next minute bar completes, so the live check every second and the walk-forward replay on minute
bars (signals/impulses.py) apply one rule: a bar whose high reached the BUY trigger would have
fired there, at the trigger price.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from gold_agent.models import Candle, Impulse

WINDOWS = (1, 3, 5, 10)  # minutes a move is measured over
VOL_MINUTES = 120  # the per-minute volatility comes from this many recent minutes
MIN_PAIRS = 60  # consecutive minute pairs needed for it
MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class Triggers:
    """What the next price is compared with until another minute bar completes."""

    at: datetime  # close of the last complete minute bar
    up: float  # a BUY impulse at or above this price
    down: float  # a SELL impulse at or below this price
    high: float  # the range high (the BUY breakout level)
    low: float  # the range low
    sigma: float  # per-minute volatility, $
    refs: tuple[tuple[int, float], ...]  # (minutes, the close that many minutes back)
    range_minutes: int


def _reference(bars: Sequence[Candle], when: datetime) -> float | None:
    """The close of the last bar that closed at or before `when`, if it is close to it in time
    (a gap of more than two minutes would measure a move over the wrong span)."""
    for bar in reversed(bars):
        if bar.close_time <= when:
            return bar.close if when - bar.close_time <= 2 * MINUTE else None
    return None


def triggers(bars: Sequence[Candle], *, z: float, range_minutes: int) -> Triggers | None:
    """From complete minute bars, oldest first; None until there is enough recent history."""
    if not bars:
        return None
    last = bars[-1].close_time
    recent = [b for b in bars[-(VOL_MINUTES + 1) :] if b.close_time > last - VOL_MINUTES * MINUTE]
    closes = np.asarray([b.close for b in recent], dtype=np.float64)
    seconds = np.asarray([b.close_time.timestamp() for b in recent])
    moves = np.diff(closes)[np.diff(seconds) == 60]  # consecutive minutes only
    if len(moves) < MIN_PAIRS:
        return None
    sigma = float(np.sqrt(np.mean(moves**2)))
    span = [b for b in recent if b.open_time >= last - range_minutes * MINUTE]
    if sigma <= 0 or not span:
        return None
    refs: list[tuple[int, float]] = []
    for minutes in WINDOWS:
        ref = _reference(recent, last - (minutes - 1) * MINUTE)
        if ref is not None:
            refs.append((minutes, ref))
    if not refs:
        return None
    high, low = max(b.high for b in span), min(b.low for b in span)
    reach = [z * sigma * math.sqrt(minutes) for minutes, _ in refs]
    up = max(high, min(ref + r for (_, ref), r in zip(refs, reach, strict=True)))
    down = min(low, max(ref - r for (_, ref), r in zip(refs, reach, strict=True)))
    return Triggers(last, up, down, high, low, sigma, tuple(refs), range_minutes)


def detect(
    trig: Triggers | None,
    price: float,
    *,
    atr: float | None,
    aligned_trend: str | None,
    since: datetime,
) -> Impulse | None:
    """The impulse at this price, or None. `atr`: ATR(14) of the chart impulse trades are sized
    on, `aligned_trend`: the M15 trend ("up"/"down"/"flat"), `since`: when it began (the caller
    keeps the first moment of a running impulse)."""
    if trig is None:
        return None
    if price >= trig.up:
        side, start, level = 1, trig.up, trig.high
    elif price <= trig.down:
        side, start, level = -1, trig.down, trig.low
    else:
        return None
    z, window, move = max(
        ((price - ref) * side / (trig.sigma * math.sqrt(minutes)), minutes, price - ref)
        for minutes, ref in trig.refs
    )
    aligned: bool | None = None
    if aligned_trend in ("up", "down"):
        aligned = (aligned_trend == "up") == (side > 0)
    elif aligned_trend == "flat":
        aligned = False
    return Impulse(
        side="BUY" if side > 0 else "SELL",
        z=round(z, 2),
        window=window,
        move=round(move, 2),
        level=round(level, 2),
        trigger=round(start, 2),
        range_minutes=trig.range_minutes,
        extension_atr=round((price - start) * side / atr, 2) if atr else None,
        aligned=aligned,
        since=since,
    )


def describe(impulse: Impulse) -> str:
    """One line for people."""
    arrow = "naik" if impulse.side == "BUY" else "turun"
    edge = "high" if impulse.side == "BUY" else "low"
    return (
        f"impuls harga {arrow} kuat: {impulse.move:+.2f}$ dalam {impulse.window} mnt "
        f"(z {impulse.z:.1f}), tembus {edge} {impulse.range_minutes} mnt {impulse.level:.2f}"
    )

"""Key levels (section 9): previous day/week, pivots, Asia range, swings, round numbers.

Candles fed here only cover XAUUSD trading hours (the market service drops ticks/bars from
weekends and the daily break, so 24/7 proxies like PAXG don't distort gold levels).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

from gold_agent.market.indicators import pivots_classic, swing_points
from gold_agent.models import Candle, Level
from gold_agent.timeutils.sessions import LONDON, SessionClock


def previous_complete(candles: Sequence[Candle], before: datetime) -> Candle | None:
    done = [c for c in candles if c.complete and c.close_time <= before]
    return done[-1] if done else None


def asia_range(
    candles: Sequence[Candle], now: datetime, sessions: SessionClock
) -> tuple[float, float] | None:
    """High/low from the trading-day open (18:00 NY) until London opens (or now)."""
    day_start = sessions.trading_day_start(now)
    london_day = (day_start + timedelta(hours=12)).astimezone(LONDON).date()
    end = min(sessions.moment("london_open", london_day), now)
    window = [c for c in candles if day_start <= c.open_time < end]
    if not window:
        return None
    return max(c.high for c in window), min(c.low for c in window)


def previous_week_hlc(
    d1: Sequence[Candle], now: datetime, sessions: SessionClock
) -> tuple[float, float, float] | None:
    """High/low/close of the previous trading week from trading-day (D1) candles."""
    this_open = sessions.last_weekly_open(now)
    previous_open = sessions.last_weekly_open(this_open - timedelta(minutes=1))
    week = [
        c
        for c in d1
        if c.complete
        and previous_open - timedelta(hours=2) <= c.open_time < this_open - timedelta(hours=1)
    ]
    if not week:
        return None
    return max(c.high for c in week), min(c.low for c in week), week[-1].close


def round_levels(price: float, steps: Sequence[float]) -> list[Level]:
    levels: list[Level] = []
    for step in steps:
        below = math.floor(price / step) * step
        levels.append(Level(name=f"angka bulat {step:g}", price=round(below, 2), kind="round"))
        levels.append(
            Level(name=f"angka bulat {step:g}", price=round(below + step, 2), kind="round")
        )
    return levels


def compute_levels(
    *,
    price: float,
    now: datetime,
    d1: Sequence[Candle],
    h1: Sequence[Candle],
    asia: tuple[float, float] | None,
    sessions: SessionClock,
    round_steps: Sequence[float],
    swing_half_width: int,
) -> list[Level]:
    levels: list[Level] = []
    prev_day = previous_complete(d1, now)
    if prev_day is not None:
        levels.append(Level(name="high kemarin", price=prev_day.high, kind="resistance"))
        levels.append(Level(name="low kemarin", price=prev_day.low, kind="support"))
        for name, value in pivots_classic(prev_day.high, prev_day.low, prev_day.close).items():
            if name in ("P", "R1", "R2", "S1", "S2"):
                levels.append(
                    Level(name=f"pivot harian {name}", price=round(value, 2), kind="pivot")
                )
    weekly = previous_week_hlc(d1, now, sessions)
    if weekly is not None:
        for name, value in pivots_classic(*weekly).items():
            if name in ("P", "R1", "S1"):
                levels.append(
                    Level(name=f"pivot mingguan {name}", price=round(value, 2), kind="pivot")
                )
    if asia is not None:
        levels.append(Level(name="high sesi Asia", price=asia[0], kind="range"))
        levels.append(Level(name="low sesi Asia", price=asia[1], kind="range"))
    recent = [c for c in h1 if c.complete][-120:]
    highs, lows = swing_points([c.high for c in recent], [c.low for c in recent], swing_half_width)
    above = sorted(p for _, p in highs if p > price)[:2]
    below = sorted((p for _, p in lows if p < price), reverse=True)[:2]
    levels += [Level(name="swing high H1", price=p, kind="resistance") for p in above]
    levels += [Level(name="swing low H1", price=p, kind="support") for p in below]
    levels += round_levels(price, round_steps)
    return sorted(levels, key=lambda lvl: lvl.price)


def nearest_above(levels: Sequence[Level], price: float, min_distance: float = 0.0) -> Level | None:
    candidates = [lvl for lvl in levels if lvl.price > price + min_distance]
    return min(candidates, key=lambda lvl: lvl.price) if candidates else None


def nearest_below(levels: Sequence[Level], price: float, min_distance: float = 0.0) -> Level | None:
    candidates = [lvl for lvl in levels if lvl.price < price - min_distance]
    return max(candidates, key=lambda lvl: lvl.price) if candidates else None

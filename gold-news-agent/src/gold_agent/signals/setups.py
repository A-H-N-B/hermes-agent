"""How past setups like the current one ended, from the stored history (the empirical half of
"peluang TP1 sebelum SL", signals/odds.py).

A setup is a bar whose close found the engine saying BUY or SELL (its stored score at or past
the entry threshold, or an active signal). Entry at that close, stop `sl_atr` ATR(14) of the
timeframe away, TP1 `tp1_r` times the risk; the following `validity_bars` bars decide: TP1
first, the stop first (a bar touching both counts as the stop, the cautious reading), or
neither. One setup at a time, like a trader: the next can only start after this one ended.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

from gold_agent.market import indicators as ind
from gold_agent.models import Candle
from gold_agent.signals.forecast_score import Held, engine_view

JSON = dict[str, Any]
Outcome = Literal["tp1", "sl", "expired"]


def _outcome(side: int, stop: float, target: float, after: Sequence[Candle]) -> tuple[Outcome, int]:
    for offset, bar in enumerate(after, start=1):
        if (bar.low <= stop) if side > 0 else (bar.high >= stop):
            return "sl", offset
        if (bar.high >= target) if side > 0 else (bar.low <= target):
            return "tp1", offset
    return "expired", len(after)


def setup_outcomes(
    *,
    bars: Sequence[Candle],
    scores: Sequence[tuple[datetime, float]],
    held: Sequence[Held],
    threshold: float,
    sl_atr: float,
    tp1_r: float,
    validity_bars: int,
    now: datetime,
    start: datetime,
    session_of: Callable[[datetime], str],
) -> JSON:
    atr = ind.atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars], 14)
    totals: dict[str, int] = {"tp1": 0, "sl": 0, "expired": 0}
    sessions: dict[str, dict[str, int]] = {}
    cursor = [0]
    i = 0
    while i < len(bars) - validity_bars:
        bar = bars[i]
        at = bar.close_time
        window = bars[i + 1 : i + 1 + validity_bars]
        contiguous = all(
            b.open_time == a.close_time for a, b in zip([bar, *window], window, strict=False)
        )
        value = float(atr[i])
        if at < start or window[-1].close_time > now or not contiguous or value != value:
            i += 1
            continue
        view = engine_view(at, scores, held, threshold, cursor)
        if view is None or abs(view) < 1:
            i += 1
            continue
        side = 1 if view > 0 else -1
        risk = sl_atr * value
        stop = bar.close - side * risk
        target = bar.close + side * tp1_r * risk
        outcome, used = _outcome(side, stop, target, window)
        totals[outcome] += 1
        tally = sessions.setdefault(session_of(at), {"tp1": 0, "sl": 0, "expired": 0})
        tally[outcome] += 1
        i += used + 1
    return {
        **totals,
        "n": sum(totals.values()),
        "by_session": {name: {**t, "n": sum(t.values())} for name, t in sorted(sessions.items())},
    }

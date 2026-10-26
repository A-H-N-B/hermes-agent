"""How past impulses ended (market/impulse.py), replayed walk-forward on the stored minute bars.

Each minute bar is checked against the triggers computed from the bars before it, the same rule
the live check applies every second: a bar whose high reached the BUY trigger (low the SELL
trigger) fired there, entered at the trigger (at the bar's open when it gapped past it). A bar
reaching both triggers is skipped (the order inside it is unknown), and so is an entry already
more than `max_extension_atr` past the trigger. The trade is sized like a live impulse signal:
stop `sl_atr` ATR(14) of the impulse chart (`sizing` bars, M5 by default) away, TP1 `tp1_r`
times the risk, valid for `validity_minutes`.
The minute bars after the entry bar decide, the stop first when one bar touches both (the
cautious reading). One trade at a time.

This record is what an impulse signal's odds blend with (signals/odds.py) and what the EV gate
reads: if past impulses on this feed lost money beyond doubt, the next one waits.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from gold_agent.market import indicators as ind
from gold_agent.market.impulse import MIN_PAIRS, VOL_MINUTES, triggers
from gold_agent.models import Candle
from gold_agent.signals.forecast_score import wilson
from gold_agent.signals.setups import JSON, Setup, resolve, tally

RECORD_DAYS = 7  # the record covers this many days of stored minute bars


def _atr_at(sizing: Sequence[Candle]) -> Callable[[datetime], float | None]:
    """ATR(14) of the last complete sizing bar closed at or before a moment."""
    done = [bar for bar in sizing if bar.complete]
    values = ind.atr([b.high for b in done], [b.low for b in done], [b.close for b in done], 14)
    closes = [bar.close_time for bar in done]

    def at(moment: datetime) -> float | None:
        index = bisect.bisect_right(closes, moment) - 1
        if index < 0:
            return None
        value = float(values[index])
        return value if value == value else None

    return at


def replay_impulses(
    *,
    m1: Sequence[Candle],
    sizing: Sequence[Candle],
    z: float,
    range_minutes: int,
    max_extension_atr: float,
    sl_atr: float,
    tp1_r: float,
    validity_minutes: int,
    now: datetime,
    start: datetime,
) -> list[Setup]:
    bars = [bar for bar in m1 if bar.complete]
    closes = [bar.close_time for bar in bars]
    atr_at = _atr_at(sizing)
    lookback = max(VOL_MINUTES, range_minutes) + 2
    validity = timedelta(minutes=validity_minutes)
    found: list[Setup] = []
    i = MIN_PAIRS
    while i < len(bars) - 1:
        bar = bars[i]
        if bar.close_time + validity > now:
            break
        after = bars[i + 1 : bisect.bisect_right(closes, bar.close_time + validity)]
        if (
            bar.open_time < start
            or bar.open_time != bars[i - 1].close_time
            or not after
            or after[0].open_time != bar.close_time  # a gap right after the entry
        ):
            i += 1
            continue
        trig = triggers(bars[max(0, i - lookback) : i], z=z, range_minutes=range_minutes)
        atr = atr_at(bar.open_time)
        up = trig is not None and bar.high >= trig.up
        down = trig is not None and bar.low <= trig.down
        if trig is None or not atr or up == down:  # nothing, or both (order unknown)
            i += 1
            continue
        side = 1 if up else -1
        level = trig.up if up else trig.down
        entry = max(level, bar.open) if up else min(level, bar.open)
        if (entry - level) * side > max_extension_atr * atr:
            i += 1
            continue
        risk = sl_atr * atr
        outcome, used = resolve(side, entry - side * risk, entry + side * tp1_r * risk, after)
        found.append(Setup(i, bar.open_time, side, outcome, after[used - 1].close_time))
        i += used + 1
    return found


def impulse_record(setups: Sequence[Setup], session_of: Callable[[datetime], str]) -> JSON:
    """The tally (setups.tally shape, so odds read it like any record) plus a summary line."""
    record = tally(setups, session_of)
    decided = record["tp1"] + record["sl"]
    interval = wilson(record["tp1"], decided)
    record["rate"] = round(record["tp1"] / decided, 4) if decided else None
    record["ci95"] = [round(interval[0], 4), round(interval[1], 4)] if interval else None
    record["buy"] = sum(1 for s in setups if s.side > 0)
    record["sell"] = len(setups) - record["buy"]
    return record


def record_line(record: JSON | None, days: int = RECORD_DAYS) -> str:
    """How past impulses ended, in one Indonesian line."""
    if not record or not record.get("n"):
        return f"belum ada impuls tercatat dalam {days} hari terakhir"
    line = (
        f"{record['n']} impuls {days} hari terakhir: {record['tp1']} kena TP1, "
        f"{record['sl']} kena SL, {record['expired']} kedaluwarsa"
    )
    if record.get("rate") is not None and record.get("ci95"):
        low, high = record["ci95"]
        line += f" (TP1 {record['rate']:.0%}, 95%: {low:.0%}–{high:.0%})"
    return line

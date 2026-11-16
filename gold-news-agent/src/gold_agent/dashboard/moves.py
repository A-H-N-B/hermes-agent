"""How far gold has moved, in percent (Live card "Naik / turun", Tanya AI, Telegram /status).

* Today: from the trading day's open (17:00 New York, the gold market's daily open; 04:00 or
  05:00 WIB) to the price now, with the day's high and low in percent of that open.
* Each recent trading day: its open to its close.
* Look-back: the price now against the price N days ago (N x 24 hours, so "7 hari" is one
  week), read from the stored bars as the last close known at that moment (a weekend gives
  Friday's close).

The page gets the reference prices from here and re-computes every percentage on each tick, so
the numbers move with the price in real time.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from gold_agent.core.app import AppContext
from gold_agent.models import Candle, MarketContext
from gold_agent.timeutils.sessions import fmt_wib

JSON = dict[str, Any]
LOOKBACK_DAYS = (1, 2, 3, 4, 5, 6, 7)  # the page's buttons; 7 = one week
MAX_LOOKBACK_DAYS = 60
DAY_ROWS = 7  # recent trading days listed under today
FINE_DAYS = 7  # look-backs this short read M15 bars, longer ones H1


def pct(price: float, reference: float) -> float:
    return round((price / reference - 1) * 100, 3)


def price_at(bars: Sequence[Candle], at: datetime) -> tuple[datetime, float] | None:
    """The last price known at `at`: the close of the newest bar closed by then. None when the
    bars start after `at` (not enough history)."""
    if not bars or bars[0].open_time > at:
        return None
    index = bisect.bisect_right([bar.close_time for bar in bars], at) - 1
    if index < 0:
        return None
    return bars[index].close_time, bars[index].close


Day = tuple[float, datetime, float, float]  # open, opened at, high, low


def day_from_bars(daily: Sequence[Candle], fine: Sequence[Candle], now: datetime) -> Day | None:
    """Today's open/high/low from stored bars, for a context that does not carry them (one
    written before the field existed, or before the live daily bar formed): the daily bar
    running now, else the M15 bars since the last complete daily bar closed."""
    for bar in reversed(daily):
        if bar.open_time <= now < bar.close_time:
            return bar.open, bar.open_time, bar.high, bar.low
    start = daily[-1].close_time if daily else None
    since = [bar for bar in fine if start is not None and start <= bar.open_time <= now]
    if not since:
        return None
    return since[0].open, since[0].open_time, max(b.high for b in since), min(b.low for b in since)


def today(context: MarketContext | None, stored: Day | None = None) -> JSON | None:
    """The trading day so far, from the open of the current daily bar (the live one in the
    context, else `stored`, read from the bars)."""
    if context is not None and context.day_open is not None and context.day_open_at is not None:
        day: Day = (
            context.day_open,
            context.day_open_at,
            context.day_high if context.day_high is not None else context.day_open,
            context.day_low if context.day_low is not None else context.day_open,
        )
    elif stored is not None:
        day = stored
    else:
        return None
    open_, opened, high, low = day
    price = context.mid if context is not None else None
    if price is not None:
        high, low = max(high, price), min(low, price)
    return {
        "open": open_,
        "open_at": opened.isoformat(),
        "open_at_wib": fmt_wib(opened, with_date=True),
        "high": high,
        "low": low,
        "price": price,
        "pct": pct(price, open_) if price is not None else None,
        "high_pct": pct(high, open_),
        "low_pct": pct(low, open_),
    }


def day_rows(daily: Sequence[Candle], before: datetime | None) -> list[JSON]:
    """Recent complete trading days (newest last), each from its open to its close."""
    days = [bar for bar in daily if bar.complete and (before is None or bar.open_time < before)]
    return [
        {
            "label": fmt_wib(bar.open_time, with_date=True).rsplit(" ", 1)[0],
            "open": bar.open,
            "close": bar.close,
            "high": bar.high,
            "low": bar.low,
            "pct": pct(bar.close, bar.open),
        }
        for bar in days[-DAY_ROWS:]
    ]


def lookback(days: int, bars: Sequence[Candle], now: datetime, price: float | None) -> JSON:
    """The price now against the price `days` x 24 hours ago."""
    at = now - timedelta(days=days)
    found = price_at(bars, at)
    if found is None:
        return {"days": days, "price": None, "note": "data belum cukup"}
    when, reference = found
    return {
        "days": days,
        "at": when.isoformat(),
        "at_wib": fmt_wib(when, with_date=True),
        "price": reference,
        "pct": pct(price, reference) if price is not None else None,
    }


def price_moves(
    *,
    context: MarketContext | None,
    daily: Sequence[Candle],
    fine: Sequence[Candle],
    coarse: Sequence[Candle],
    now: datetime,
    extra_days: int | None = None,
) -> JSON:
    """Everything the "Naik / turun" card shows. `fine` = M15 bars (look-backs up to FINE_DAYS),
    `coarse` = H1 bars (longer ones), both oldest first."""
    price = context.mid if context else None
    day = today(context, day_from_bars(daily, fine, now))
    opened = datetime.fromisoformat(day["open_at"]) if day else None
    wanted: list[int] = sorted({*LOOKBACK_DAYS, *([extra_days] if extra_days else [])})
    return {
        "price": price,
        "market_open": bool(context and context.market_open),
        "source": context.source if context else None,
        "today": day,
        "days": day_rows(daily, opened),
        "lookbacks": [lookback(n, fine if n <= FINE_DAYS else coarse, now, price) for n in wanted],
    }


def move_line(moves: JSON) -> str | None:
    """One line of words for Telegram and the chat brief, e.g. "hari ini +0.85% sejak buka
    Kam 13 Nov 05:00 WIB; 1 hari -0.20%, 3 hari +1.10%, 7 hari +2.30%"."""
    day = moves.get("today")
    parts: list[str] = []
    if day and day.get("pct") is not None:
        parts.append(
            f"hari ini {day['pct']:+.2f}% sejak buka {day['open_at_wib']} WIB "
            f"({day['open']:.2f}; tertinggi {day['high_pct']:+.2f}%, terendah "
            f"{day['low_pct']:+.2f}%)"
        )
    spans = [
        f"{row['days']} hari {row['pct']:+.2f}%"
        for row in moves.get("lookbacks", [])
        if row.get("pct") is not None and row["days"] in (1, 3, 7)
    ]
    if spans:
        parts.append(", ".join(spans))
    return "; ".join(parts) or None


async def stored_moves(ctx: AppContext, context: MarketContext | None) -> JSON:
    """The same figures from the stored bars, for Telegram and the chat (the dashboard reads
    its own per-minute bar cache)."""
    source = context.source if context else None
    market = ctx.market
    return price_moves(
        context=context,
        daily=await market.latest_candles("D1", DAY_ROWS + 2, source=source),
        fine=await market.latest_candles("M15", 4 * 24 * (FINE_DAYS + 1), source=source),
        coarse=[],
        now=ctx.clock.now(),
    )

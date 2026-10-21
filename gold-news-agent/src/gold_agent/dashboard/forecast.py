"""Timeframe ring (M1 ... MN) for the Live chart and the agent's forecast for the NEXT candle.

Picking M1 asks "what does the agent expect over the next minute", H4 "over the next four hours",
and so on. The forecast blends two things the agent already computes, with fixed, visible weights:

* the news-driven engine (composite score of news + macro + technical + price confirmation; an
  active signal counts at full strength in its direction): weight 0.6;
* that timeframe's own indicator vote (trend, EMAs, RSI, Stoch, SAR): weight 0.4.

The expected range is that timeframe's ATR(14), shifted by the score. A high-impact release
inside the horizon makes it "TUNGGU DATA" (the engine's own rule), a closed market "PASAR TUTUP".
The strength is a score, not a calibrated probability: the paper-trading stats say how good it is.

W1 and MN are built here from D1 bars (weeks by New York trading date, months by calendar month),
so they only reach back as far as the stored daily history.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from gold_agent.config import Settings
from gold_agent.dashboard.indicators_view import momentum, timeframe_read
from gold_agent.market.context import timeframe_state
from gold_agent.models import CalendarEvent, Candle, MarketContext, Signal, SignalState
from gold_agent.models.market import TimeframeState
from gold_agent.timeutils.sessions import NEW_YORK, fmt_wib

JSON = dict[str, Any]
RING = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN")
SPAN = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "M30": timedelta(minutes=30),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
    "W1": timedelta(days=7),
    "MN": timedelta(days=30),  # display horizon; month bars follow the calendar
}
HORIZON_ID = {
    "M1": "1 menit",
    "M5": "5 menit",
    "M15": "15 menit",
    "M30": "30 menit",
    "H1": "1 jam",
    "H4": "4 jam",
    "D1": "1 hari",
    "W1": "1 minggu",
    "MN": "1 bulan",
}
NEWS_WEIGHT, CHART_WEIGHT = 0.6, 0.4
NEUTRAL_BAND = 0.2  # |score| below this reads NETRAL


def _bucket(bar: Candle, tf: str) -> tuple[int, int]:
    day = bar.close_time.astimezone(NEW_YORK).date()  # D1 closes at 17:00 New York
    if tf == "W1":
        year, week, _ = day.isocalendar()
        return year, week
    return day.year, day.month


def aggregate(daily: Sequence[Candle], tf: str) -> list[Candle]:
    """D1 bars -> W1 (ISO week of the New York trading date) or MN (calendar month)."""
    out: list[Candle] = []
    key: tuple[int, int] | None = None
    for bar in daily:
        k = _bucket(bar, tf)
        if out and k == key:
            last = out[-1]
            out[-1] = last.model_copy(
                update={
                    "high": max(last.high, bar.high),
                    "low": min(last.low, bar.low),
                    "close": bar.close,
                    "close_time": bar.close_time,
                    "volume": last.volume + bar.volume,
                    "complete": bar.complete,
                }
            )
        else:
            out.append(bar.model_copy(update={"tf": "D1"}))
            key = k
    return out


def chart_state(tf: str, bars: Sequence[Candle]) -> TimeframeState:
    """The market service's own TimeframeState (same indicators, same trend rule) for any
    timeframe's bars; W1/MN only relabel it, the computation does not depend on the name."""
    return timeframe_state("D1", bars).model_copy(update={"tf": tf})


def news_score(state: SignalState | None, active: Signal | None, threshold: float) -> float:
    """The engine's view in [-1, 1]: an active signal is full strength, otherwise the composite
    score relative to the entry threshold."""
    if active is not None:
        return 1.0 if active.action == "BUY" else -1.0
    if state is None:
        return 0.0
    return max(-1.0, min(1.0, state.composite_score / threshold))


def forecast_next(
    *,
    tf: str,
    bars: Sequence[Candle],
    now: datetime,
    settings: Settings,
    state: SignalState | None,
    active: Signal | None,
    events: Sequence[CalendarEvent],
    context: MarketContext | None,
) -> JSON:
    base: JSON = {"tf": tf, "horizon": HORIZON_ID[tf]}
    if not bars:
        return base | {"direction": "DATA BELUM ADA", "reasons": ["belum ada candle untuk " + tf]}
    last_bar = bars[-1]
    until = last_bar.open_time + SPAN[tf] if tf != "MN" else last_bar.close_time
    if until <= now:
        until = now + SPAN[tf]
    read = timeframe_read(chart_state(tf, bars), momentum(bars))
    chart = read["score"] / read["votes"] if read["votes"] else 0.0
    news = news_score(state, active, settings.signal_engine.entry_threshold)
    score = NEWS_WEIGHT * news + CHART_WEIGHT * chart
    atr = read["atr14"]
    close = last_bar.close
    reasons = [
        f"berita/engine {news:+.2f} × {NEWS_WEIGHT}",
        f"indikator {tf} {chart:+.2f} × {CHART_WEIGHT} ({', '.join(read['reasons']) or 'netral'})",
    ]
    confirm = timedelta(seconds=settings.trading.post_release_confirm_seconds)
    release = next(
        (
            e
            for e in sorted(events, key=lambda e: e.scheduled_at)
            if e.impact == "high" and not e.all_day and now - confirm <= e.scheduled_at <= until
        ),
        None,
    )
    if context is not None and not context.market_open:
        direction = "PASAR TUTUP"
    elif release is not None:
        direction = "TUNGGU DATA"
        reasons.insert(
            0, f"rilis {release.country} {release.title} {fmt_wib(release.scheduled_at)} WIB"
        )
    elif abs(score) < NEUTRAL_BAND:
        direction = "NETRAL"
    else:
        direction = "NAIK" if score > 0 else "TURUN"
    expected = None
    if atr is not None and not math.isnan(atr):
        centre = close + score * atr * 0.5
        expected = {
            "low": round(centre - atr / 2, 2),
            "high": round(centre + atr / 2, 2),
            "target": round(centre, 2),
        }
    return base | {
        "direction": direction,
        "strength": round(100 * abs(score)),
        "score": round(score, 3),
        "close": close,
        "atr": round(atr, 2) if atr is not None else None,
        "expected": expected,
        "until_utc": until.isoformat(),
        "until_wib": fmt_wib(until, with_date=tf in ("D1", "W1", "MN")),
        "reasons": reasons,
    }

"""How often the next-candle forecast was right, measured walk-forward on the stored history.

For every closed bar of a timeframe the forecast is rebuilt as it stood at that bar's close: the
chart vote from the bars up to it, and the engine's score as stored just before (an active
signal counts in full). The next bar's close says whether NAIK / TURUN was right. NETRAL and
TUNGGU DATA are abstentions, not misses. Bars with no stored engine score (agent not running)
are skipped. Results are also split by trading session (Asia, London, overlap, New York), which
is what the automatic timeframe choice reads. A coin flip scores 50%, so the 95% interval says
whether there is a real edge.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from gold_agent.market import indicators as ind
from gold_agent.market.context import trend_of
from gold_agent.market.reads import SAR, STOCH, timeframe_read
from gold_agent.models import CalendarEvent, Candle
from gold_agent.models.market import TimeframeState
from gold_agent.signals.forecast import CHART_WEIGHT, NEUTRAL_BAND, NEWS_WEIGHT, SPAN

JSON = dict[str, Any]
Held = tuple[datetime, datetime, float]  # an active signal: from, until (or its close), ±1
STATE_MAX_AGE = timedelta(minutes=5)  # an older stored score means the agent was not running
Z95 = 1.96


def wilson(hits: int, n: int) -> tuple[float, float] | None:
    """95% Wilson score interval for a hit rate."""
    if n == 0:
        return None
    p = hits / n
    centre = (p + Z95**2 / (2 * n)) / (1 + Z95**2 / n)
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n * n)) / (1 + Z95**2 / n)
    return centre - half, centre + half


def _value(series: Any, i: int) -> float | None:
    value = float(series[i])
    return None if math.isnan(value) else value


def chart_scores(tf: str, bars: Sequence[Candle]) -> list[float]:
    """The forecast's chart vote in [-1, 1] at each bar's close (causal indicators)."""
    closes = [b.close for b in bars]
    highs, lows = [b.high for b in bars], [b.low for b in bars]
    ema20, ema50, ema200 = ind.ema(closes, 20), ind.ema(closes, 50), ind.ema(closes, 200)
    rsi, atr = ind.rsi(closes, 14), ind.atr(highs, lows, closes, 14)
    stoch_k, stoch_d = ind.stochastic(highs, lows, closes, *STOCH)
    sar, sar_dir = ind.parabolic_sar(highs, lows, *SAR)
    scores: list[float] = []
    for i, close in enumerate(closes):
        fast, slow = _value(ema20, i), _value(ema50, i)
        state = TimeframeState.model_construct(
            tf=tf,
            close=close,
            ema20=fast,
            ema50=slow,
            ema200=_value(ema200, i),
            rsi14=_value(rsi, i),
            atr14=_value(atr, i),
            trend=trend_of(close, fast, slow),
        )
        has_sar = _value(sar, i) is not None
        extra = {
            "stoch_k": _value(stoch_k, i),
            "stoch_d": _value(stoch_d, i),
            "sar_up": bool(sar_dir[i] > 0) if has_sar else None,
        }
        read = timeframe_read(state, extra)
        scores.append(read["score"] / read["votes"] if read["votes"] else 0.0)
    return scores


@dataclass
class Tally:
    calls: int = 0
    hits: int = 0

    def add(self, hit: bool) -> None:
        self.calls += 1
        self.hits += hit

    def to_json(self) -> JSON:
        interval = wilson(self.hits, self.calls)
        return {
            "calls": self.calls,
            "hits": self.hits,
            "rate": round(self.hits / self.calls, 4) if self.calls else None,
            "ci95": [round(interval[0], 4), round(interval[1], 4)] if interval else None,
        }


def _news(
    at: datetime,
    scores: Sequence[tuple[datetime, float]],
    held: Sequence[Held],
    threshold: float,
    cursor: list[int],
) -> float | None:
    """Engine view at `at` (same rule as the live forecast), None when nothing was stored."""
    started = bisect.bisect_right(held, at, key=lambda h: h[0])
    for start, until, sign in held[max(0, started - 2) : started]:
        if start <= at < until:
            return sign
    i = cursor[0]
    while i + 1 < len(scores) and scores[i + 1][0] <= at:
        i += 1
    cursor[0] = i
    if not scores or scores[i][0] > at or at - scores[i][0] > STATE_MAX_AGE:
        return None
    return max(-1.0, min(1.0, scores[i][1] / threshold))


def forecast_accuracy(
    *,
    tf: str,
    bars: Sequence[Candle],
    scores: Sequence[tuple[datetime, float]],
    held: Sequence[Held],
    events: Sequence[CalendarEvent],
    threshold: float,
    confirm: timedelta,
    now: datetime,
    start: datetime,
    session_of: Callable[[datetime], str],
) -> JSON:
    """Hit rate of the next-candle direction, in total and per session (the session of the bar
    whose close made the call), plus the news-only / chart-only parts on the same bars.
    `scores` and `held` sorted by time."""
    step = SPAN[tf]
    chart = chart_scores(tf, bars)
    releases = sorted(e.scheduled_at for e in events if e.impact == "high" and not e.all_day)
    blend, news_only, chart_only = Tally(), Tally(), Tally()
    sessions: dict[str, Tally] = {}
    abstained = skipped = 0
    cursor = [0]
    for i in range(len(bars) - 1):
        bar, after = bars[i], bars[i + 1]
        at = bar.close_time
        if at < start or after.open_time != at or after.close_time > now:
            continue  # warm-up, a gap (closed market, missing data), or the next bar still open
        news = _news(at, scores, held, threshold, cursor)
        if news is None:
            skipped += 1
            continue
        if any(at - confirm <= release <= at + step for release in releases):
            abstained += 1  # TUNGGU DATA
            continue
        moved = after.close - bar.close
        score = NEWS_WEIGHT * news + CHART_WEIGHT * chart[i]
        if abs(score) < NEUTRAL_BAND:
            abstained += 1
        else:
            blend.add(score * moved > 0)
            sessions.setdefault(session_of(at), Tally()).add(score * moved > 0)
        if news:
            news_only.add(news * moved > 0)
        if chart[i]:
            chart_only.add(chart[i] * moved > 0)
    return {
        "tf": tf,
        "since": start.isoformat(),
        "forecast": blend.to_json(),
        "by_session": {name: tally.to_json() for name, tally in sorted(sessions.items())},
        "news_only": news_only.to_json(),
        "chart_only": chart_only.to_json(),
        "abstained": abstained,
        "skipped_no_engine": skipped,
    }

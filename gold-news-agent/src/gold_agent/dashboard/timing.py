"""'Kapan klik BUY/SELL': the agent's answer to "what do I do, and when", as absolute times.

Three things, all from what the engine and the outlook already decided (nothing new is predicted
here): the action right now (an active signal and how long it stays valid, or WAIT and why), the
no-trade window around the next high-impact release (the engine's own blackout + post-release
confirmation), and the next outlook hours that lean BUY or SELL with the price that would confirm
them, plus the chart trading uses now (chosen automatically, signals/timeframe.py). The browser
turns the absolute times into live countdowns against the WIB clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from gold_agent.config import Settings
from gold_agent.market.levels import nearest_above, nearest_below
from gold_agent.models import CalendarEvent, MarketContext, Outlook, Signal, SignalState
from gold_agent.signals.outlook import CLOSED
from gold_agent.timeutils.sessions import fmt_wib

JSON = dict[str, Any]
MAX_WINDOWS = 4


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def bias_action(bias: str) -> str | None:
    """Outlook bias text -> BUY / SELL / None (neutral, TUNGGU DATA, closed)."""
    text = bias.lower()
    if bias == "BUY" or "naik" in text:
        return "BUY"
    if bias == "SELL" or "turun" in text:
        return "SELL"
    return None


def _now_block(active: Signal | None, state: SignalState | None, now: datetime) -> JSON:
    if active is not None and active.valid_until_utc > now:
        return {
            "mode": "signal",
            "action": active.action,
            "entry_low": active.entry_low,
            "entry_high": active.entry_high,
            "entry_type": active.entry_type,
            "stop_loss": active.stop_loss,
            "tp1": active.tp1,
            "tp2": active.tp2,
            "until_utc": _iso(active.valid_until_utc),
            "until_wib": fmt_wib(active.valid_until_utc),
            "confidence": active.confidence,
            "score": state.composite_score if state else None,
        }
    return {
        "mode": "wait",
        "reasons": state.wait_reasons if state else ["status sinyal belum tersedia"],
        "score": state.composite_score if state else None,
        "confidence": state.confidence if state else None,
    }


def _blackout(events: Sequence[CalendarEvent], now: datetime, settings: Settings) -> JSON | None:
    window = timedelta(minutes=settings.signal_engine.event_blackout_minutes)
    confirm = timedelta(seconds=settings.trading.post_release_confirm_seconds)
    upcoming = sorted(
        (
            e
            for e in events
            if e.impact == "high" and not e.all_day and e.scheduled_at + confirm >= now
        ),
        key=lambda e: e.scheduled_at,
    )
    if not upcoming:
        return None
    event = upcoming[0]
    start, end = event.scheduled_at - window, event.scheduled_at + confirm
    return {
        "title": f"{event.country} {event.title}",
        "at_utc": _iso(event.scheduled_at),
        "at_wib": fmt_wib(event.scheduled_at),
        "from_utc": _iso(start),
        "from_wib": fmt_wib(start),
        "to_utc": _iso(end),
        "to_wib": fmt_wib(end),
        "active": start <= now <= end,
    }


def _timeframe(
    state: SignalState | None, active: Signal | None, choice: JSON | None
) -> JSON | None:
    """The chart trading uses now and why (signals/timeframe.py, or the AI's pick for the active
    signal), with the per-timeframe table the choice was made from."""
    options = (choice or {}).get("options")
    if active is not None and active.timeframe:
        by_ai = active.review is not None and active.review.model is not None
        return {
            "tf": active.timeframe,
            "reason": active.timeframe_reason,
            "source": "opus" if by_ai else "signal",
            "options": options,
        }
    if state is not None and state.timeframe:
        return {
            "tf": state.timeframe,
            "reason": state.timeframe_reason,
            "source": "auto",
            "options": options,
        }
    return None


def _windows(outlook: Outlook | None, now: datetime) -> list[JSON]:
    if outlook is None:
        return []
    hour = now.replace(minute=0, second=0, microsecond=0)
    out: list[JSON] = []
    for row in outlook.rows:
        # Hours covered by the active signal (bias exactly BUY/SELL) are the "now" block already.
        if row.hour_start_utc < hour or row.bias in (CLOSED, "BUY", "SELL"):
            continue
        action = bias_action(row.bias)
        if action is None:
            continue
        out.append(
            {
                "action": action,
                "from_utc": _iso(max(row.hour_start_utc, now)),
                "to_utc": _iso(row.hour_start_utc + timedelta(hours=1)),
                "label_wib": row.label_wib,
                "confidence": row.confidence,
                "conditional": row.conditional,
                "scenario": row.scenario,
                "events": row.events,
            }
        )
        if len(out) == MAX_WINDOWS:
            break
    return out


def _triggers(context: MarketContext | None) -> JSON | None:
    if context is None or context.mid is None:
        return None
    above = nearest_above(context.levels, context.mid)
    below = nearest_below(context.levels, context.mid)
    if above is None and below is None:
        return None
    return {
        "mid": context.mid,
        "buy_above": {"price": above.price, "name": above.name} if above else None,
        "sell_below": {"price": below.price, "name": below.name} if below else None,
    }


def timing_view(
    *,
    now: datetime,
    settings: Settings,
    active: Signal | None,
    state: SignalState | None,
    outlook: Outlook | None,
    events: Sequence[CalendarEvent],
    context: MarketContext | None,
    choice: JSON | None = None,
) -> JSON:
    """`choice`: the last timeframe choice the signal service stored (its per-TF table)."""
    return {
        "now_utc": _iso(now),
        "now": _now_block(active, state, now),
        "threshold": settings.signal_engine.entry_threshold,
        "timeframe": _timeframe(state, active, choice),
        "blackout": _blackout(events, now, settings),
        "windows": _windows(outlook, now),
        "triggers": _triggers(context),
    }

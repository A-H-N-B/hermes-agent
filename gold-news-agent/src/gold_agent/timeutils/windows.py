"""The user's trading hours (`trading.windows`): which window is open now and which opens next.

Each window is a wall-clock span in its own zone, resolved per date with zoneinfo: one set in
Asia/Jakarta stays on the WIB clock, one set in Europe/London follows British DST. A window whose
whole span falls while XAUUSD is closed (the weekend) does not count.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from gold_agent.config import TradingWindow
from gold_agent.timeutils.sessions import SessionClock, fmt_wib, local_to_utc, to_wib

SEARCH_DAYS = 8  # a weekly gap (Friday night -> Monday) is the longest wait for a window


@dataclass(frozen=True)
class WindowSpan:
    name: str
    timeframe: str
    start: datetime  # UTC
    end: datetime

    @property
    def label(self) -> str:
        """'London M5 14:00–17:00 WIB'"""
        return f"{self.name} {self.timeframe} {fmt_wib(self.start)}–{fmt_wib(self.end)} WIB"

    def label_from(self, now: datetime) -> str:
        """The label, with the day in front when the window opens on another WIB date."""
        if to_wib(self.start).date() == to_wib(now).date():
            return self.label
        start = fmt_wib(self.start, with_date=True)
        return f"{self.name} {self.timeframe} {start}–{fmt_wib(self.end)} WIB"

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "timeframe": self.timeframe,
            "start_utc": self.start.isoformat(),
            "end_utc": self.end.isoformat(),
            "start_wib": fmt_wib(self.start, with_date=True),
            "end_wib": fmt_wib(self.end),
            "label": self.label,
        }


class TradingHours:
    def __init__(self, windows: Sequence[TradingWindow], sessions: SessionClock) -> None:
        self._windows = tuple(windows)
        self._sessions = sessions

    @property
    def enabled(self) -> bool:
        return bool(self._windows)

    def spans(self, start: datetime, end: datetime) -> list[WindowSpan]:
        """Every tradable window overlapping [start, end), sorted by opening time."""
        found: list[WindowSpan] = []
        for window in self._windows:
            zone = ZoneInfo(window.zone)
            day = start.astimezone(zone).date() - timedelta(days=1)
            while day <= end.astimezone(zone).date():
                opens = local_to_utc(day, window.start, zone)
                closes_on = day + timedelta(days=1) if window.end < window.start else day
                closes = local_to_utc(closes_on, window.end, zone)
                if closes > start and opens < end and self._tradable(opens, closes):
                    found.append(WindowSpan(window.name, window.timeframe, opens, closes))
                day += timedelta(days=1)
        return sorted(found, key=lambda span: span.start)

    def _tradable(self, opens: datetime, closes: datetime) -> bool:
        last_minute = closes - timedelta(minutes=1)
        return self._sessions.status(opens).open or self._sessions.status(last_minute).open

    def current(self, now: datetime) -> WindowSpan | None:
        open_now = self.spans(now, now + timedelta(seconds=1))
        return next((s for s in open_now if s.start <= now), None)

    def next(self, now: datetime) -> WindowSpan | None:
        upcoming = self.spans(now, now + timedelta(days=SEARCH_DAYS))
        return next((s for s in upcoming if s.start > now), None)

    def summary(self, now: datetime) -> str:
        """'London M5 14:00–17:00, New York M15 19:00–23:00 WIB' (times as of today)."""
        parts: list[str] = []
        for window in self._windows:
            zone = ZoneInfo(window.zone)
            day = now.astimezone(zone).date()
            opens = local_to_utc(day, window.start, zone)
            closes_on = day + timedelta(days=1) if window.end < window.start else day
            closes = local_to_utc(closes_on, window.end, zone)
            parts.append(f"{window.name} {window.timeframe} {fmt_wib(opens)}–{fmt_wib(closes)}")
        return ", ".join(parts) + " WIB" if parts else "sepanjang hari"

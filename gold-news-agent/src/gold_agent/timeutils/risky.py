"""Risky windows (11.5): around big releases, rollover, Friday close, Monday open, holidays."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time, timedelta

from gold_agent.models import CalendarEvent
from gold_agent.timeutils.sessions import NEW_YORK, SessionClock, uk_holiday, us_holiday


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start <= b_end and b_start <= a_end


def _ny(day_offset: int, base: datetime, hhmm: str) -> datetime:
    local_day = base.astimezone(NEW_YORK).date() + timedelta(days=day_offset)
    return datetime.combine(local_day, time.fromisoformat(hhmm), tzinfo=NEW_YORK)


def risky_flags(
    start: datetime,
    end: datetime,
    *,
    clock: SessionClock,
    events: Sequence[CalendarEvent],
    release_window_minutes: int = 15,
    monday_open_minutes: int = 60,
) -> list[str]:
    """Reasons (Bahasa Indonesia) why [start, end] is a risky time to trade."""
    flags: list[str] = []
    window = timedelta(minutes=release_window_minutes)
    for event in events:
        if event.impact != "high" or event.all_day:
            continue
        if _overlaps(start, end, event.scheduled_at - window, event.scheduled_at + window):
            flags.append(f"±{release_window_minutes} mnt rilis {event.country} {event.title}")
    for offset in (-1, 0):
        roll_start = _ny(offset, start, "16:45")
        roll_end = _ny(offset, start, "18:00")
        if _overlaps(start, end, roll_start, roll_end):
            flags.append("sekitar rollover harian: spread melebar, likuiditas tipis")
            break
    ny_start = start.astimezone(NEW_YORK)
    if ny_start.weekday() == 4 and _overlaps(
        start, end, _ny(0, start, "15:00"), _ny(0, start, "17:00")
    ):
        flags.append("Jumat menjelang tutup pekan")
    weekly_open = clock.last_weekly_open(end)
    if _overlaps(start, end, weekly_open, weekly_open + timedelta(minutes=monday_open_minutes)):
        flags.append("pembukaan pekan: risiko gap")
    names = {f"libur AS ({h})" for h in [us_holiday(ny_start.date())] if h}
    names |= {f"libur UK ({h})" for h in [uk_holiday(start.date())] if h}
    flags.extend(sorted(names))
    return flags

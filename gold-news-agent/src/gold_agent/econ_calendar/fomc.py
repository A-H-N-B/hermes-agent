"""FOMC meeting dates (federalreserve.gov calendar page) and the Fed communications blackout.

Blackout: from midnight ET on the second Saturday before a meeting begins until midnight ET at
the end of the day after the meeting ends. During it, "Fed speak" headlines deserve suspicion.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from gold_agent.timeutils.sessions import NEW_YORK

_MONTH_NAMES = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_SECTION = re.compile(r"(?P<year>20\d{2})\s+FOMC\s+Meetings", re.I)
_MEETING = re.compile(
    rf"(?P<m1>{_MONTH})(?:\s*/\s*(?P<m2>{_MONTH}))?\s+(?P<d1>\d{{1,2}})"
    r"(?:\s*[-–]\s*(?P<d2>\d{1,2}))?(?P<sep>\*)?",
    re.I,
)


def _month(name: str) -> int:
    prefix = name.lower()[:3]
    return next(i for i, full in enumerate(_MONTH_NAMES, start=1) if full.startswith(prefix))


_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class FomcMeeting:
    start: date
    end: date
    sep: bool  # Summary of Economic Projections (dot plot) meeting


def parse_fomc_calendar(page: str) -> list[FomcMeeting]:
    """Tolerant parser over the page text; verify against a recorded copy of the real page."""
    text = re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", page)))
    sections = list(_SECTION.finditer(text))
    meetings: list[FomcMeeting] = []
    for index, section in enumerate(sections):
        year = int(section.group("year"))
        body_end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
        body = text[section.end() : body_end]
        for match in _MEETING.finditer(body):
            first_month = _month(match.group("m1"))
            last_month = _month(match.group("m2") or match.group("m1"))
            d1 = int(match.group("d1"))
            d2 = int(match.group("d2") or d1)
            try:
                start = date(year, first_month, d1)
                end = date(year, last_month, d2)
            except ValueError:
                continue
            if end < start or (end - start).days > 3:
                continue
            meetings.append(FomcMeeting(start, end, bool(match.group("sep"))))
    unique = {(m.start, m.end): m for m in meetings}
    return sorted(unique.values(), key=lambda m: m.start)


def blackout_window(meeting: FomcMeeting) -> tuple[datetime, datetime]:
    """(start, end) in UTC-aware datetimes of the communications blackout around a meeting."""
    days_since_saturday = (meeting.start.weekday() - 5) % 7 or 7
    first_saturday_before = meeting.start - timedelta(days=days_since_saturday)
    second_saturday_before = first_saturday_before - timedelta(days=7)
    start = datetime.combine(second_saturday_before, time(0, 0), tzinfo=NEW_YORK)
    end = datetime.combine(meeting.end + timedelta(days=2), time(0, 0), tzinfo=NEW_YORK)
    return start, end


def in_blackout(moment: datetime, meetings: list[FomcMeeting]) -> FomcMeeting | None:
    for meeting in meetings:
        start, end = blackout_window(meeting)
        if start <= moment < end:
            return meeting
    return None

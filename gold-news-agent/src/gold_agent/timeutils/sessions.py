"""Market sessions (11.1) computed per date from real time zones — never fixed offsets.

XAUUSD trades Sunday 18:00 → Friday 17:00 New York time, with a daily break 17:00–18:00
New York. Session labels and liquidity factors drive the signal engine and the outlook.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

import holidays

from gold_agent.config import SessionSettings, WeeklyMoment

NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
WIB = ZoneInfo("Asia/Jakarta")

SessionName = Literal["asia", "london", "overlap", "new_york", "closed"]
SESSION_LABEL_ID: dict[str, str] = {
    "asia": "Asia",
    "london": "London",
    "overlap": "London–New York",
    "new_york": "New York",
    "closed": "Pasar tutup",
}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def local_to_utc(day: date, hhmm: str, zone: ZoneInfo) -> datetime:
    """Wall-clock time on a local date -> UTC (zoneinfo resolves DST for that date)."""
    local = datetime.combine(day, time.fromisoformat(hhmm), tzinfo=zone)
    return local.astimezone(UTC)


def to_wib(moment: datetime) -> datetime:
    return moment.astimezone(WIB)


_DAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_MONTHS_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def fmt_wib(moment: datetime, with_date: bool = False) -> str:
    """'19:30' or 'Selasa 13 Okt 19:30' (Indonesian day/month names, WIB)."""
    local = to_wib(moment)
    if not with_date:
        return local.strftime("%H:%M")
    return f"{_DAYS_ID[local.weekday()]} {local.day} {_MONTHS_ID[local.month - 1]} {local:%H:%M}"


def last_weekly(spec: WeeklyMoment, moment: datetime) -> datetime:
    """Latest occurrence (UTC) of a weekly wall-clock moment at or before `moment`."""
    zone = ZoneInfo(spec.zone)
    weekday = _WEEKDAYS.index(spec.weekday)
    local_day = moment.astimezone(zone).date()
    day = local_day - timedelta(days=(local_day.weekday() - weekday) % 7)
    candidate = local_to_utc(day, spec.local, zone)
    if candidate > moment:  # same weekday but later today: use last week's (own DST)
        candidate = local_to_utc(day - timedelta(days=7), spec.local, zone)
    return candidate


@lru_cache(maxsize=16)
def _us_holidays(year: int) -> dict[date, str]:
    return dict(holidays.financial_holidays("NYSE", years=[year]))


@lru_cache(maxsize=16)
def _uk_holidays(year: int) -> dict[date, str]:
    return dict(holidays.country_holidays("GB", subdiv="ENG", years=[year]))


def us_holiday(day: date) -> str | None:
    return _us_holidays(day.year).get(day)


def uk_holiday(day: date) -> str | None:
    return _uk_holidays(day.year).get(day)


@dataclass(frozen=True)
class MarketStatus:
    open: bool
    reason: str | None  # Bahasa Indonesia, None when open
    session: SessionName
    liquidity_factor: float
    holidays: tuple[str, ...] = ()


class SessionClock:
    def __init__(self, settings: SessionSettings) -> None:
        self._settings = settings
        self._moments = {m.id: m for m in settings.moments}

    # ------------------------------------------------------------------ moments

    def moment(self, moment_id: str, day: date) -> datetime:
        """UTC time of a named moment (e.g. 'london_open') on the local date `day`."""
        spec = self._moments[moment_id]
        return local_to_utc(day, spec.local, ZoneInfo(spec.zone))

    def moments_between(self, start: datetime, end: datetime) -> list[tuple[str, datetime]]:
        """All named moments in [start, end), sorted (local dates are scanned with slack)."""
        found: list[tuple[str, datetime]] = []
        day = (start - timedelta(days=1)).date()
        last = (end + timedelta(days=1)).date()
        while day <= last:
            for moment_id in self._moments:
                at = self.moment(moment_id, day)
                if start <= at < end:
                    found.append((moment_id, at))
            day += timedelta(days=1)
        return sorted(found, key=lambda pair: pair[1])

    # ------------------------------------------------------------------ trading day

    def trading_day_start(self, moment: datetime) -> datetime:
        """Start of the gold trading day containing `moment`: 17:00 New York the day before
        (D1/H4 candles and session VWAP are anchored here, matching broker charts)."""
        ny = moment.astimezone(NEW_YORK)
        anchor = datetime.combine(ny.date(), time(17, 0), tzinfo=NEW_YORK)
        if ny < anchor:
            anchor = datetime.combine(ny.date() - timedelta(days=1), time(17, 0), tzinfo=NEW_YORK)
        return anchor.astimezone(UTC)

    def _weekly_spec(self, which: Literal["open", "close"]) -> tuple[ZoneInfo, int, str]:
        spec = self._settings.weekly_open if which == "open" else self._settings.weekly_close
        return ZoneInfo(spec.zone), _WEEKDAYS.index(spec.weekday), spec.local

    def _weekly_last(self, which: Literal["open", "close"], moment: datetime) -> datetime:
        spec = self._settings.weekly_open if which == "open" else self._settings.weekly_close
        return last_weekly(spec, moment)

    def last_weekly_open(self, moment: datetime) -> datetime:
        return self._weekly_last("open", moment)

    def last_weekly_close(self, moment: datetime) -> datetime:
        return self._weekly_last("close", moment)

    def next_weekly_open(self, moment: datetime) -> datetime:
        zone, weekday, hhmm = self._weekly_spec("open")
        local_day = moment.astimezone(zone).date()
        day = local_day + timedelta(days=(weekday - local_day.weekday()) % 7)
        candidate = local_to_utc(day, hhmm, zone)
        if candidate <= moment:
            candidate = local_to_utc(day + timedelta(days=7), hhmm, zone)
        return candidate

    def is_weekend(self, moment: datetime) -> bool:
        """True between Friday's weekly close and Sunday's weekly open."""
        return self.last_weekly_close(moment) > self.last_weekly_open(moment)

    def in_daily_break(self, moment: datetime) -> bool:
        brk = self._settings.daily_break
        zone = ZoneInfo(brk.zone)
        local = moment.astimezone(zone)
        start = datetime.combine(local.date(), time.fromisoformat(brk.start), tzinfo=zone)
        end = datetime.combine(local.date(), time.fromisoformat(brk.end), tzinfo=zone)
        return start <= local < end

    # ------------------------------------------------------------------ sessions

    def session(self, moment: datetime) -> SessionName:
        if self.is_weekend(moment) or self.in_daily_break(moment):
            return "closed"
        # London and New York daytime sessions share a calendar date, so anchor every
        # boundary on the London date (mixing dates misreads late-NY / early-Asia hours).
        day = moment.astimezone(LONDON).date()
        london_open = self.moment("london_open", day)
        london_close = self.moment("london_close", day)
        ny_open = self.moment("ny_open", day)
        ny_close = self.moment("daily_close", day)
        if ny_open <= moment < london_close:
            return "overlap"
        if london_open <= moment < ny_open:
            return "london"
        if london_close <= moment < ny_close and moment >= ny_open:
            return "new_york"
        return "asia"

    def status(self, moment: datetime) -> MarketStatus:
        session = self.session(moment)
        day_ny = moment.astimezone(NEW_YORK).date()
        day_uk = moment.astimezone(LONDON).date()
        names = tuple(
            f"{label}: {name}"
            for label, name in (("AS", us_holiday(day_ny)), ("UK", uk_holiday(day_uk)))
            if name
        )
        if session == "closed":
            reason = "akhir pekan" if self.is_weekend(moment) else "jeda harian (rollover)"
            return MarketStatus(False, reason, session, 0.0, names)
        factor = self._settings.liquidity.get(session, 1.0)
        if names:
            factor *= self._settings.holiday_factor
        return MarketStatus(True, None, session, round(factor, 3), names)

    def label(self, moment: datetime) -> str:
        return SESSION_LABEL_ID[self.session(moment)]

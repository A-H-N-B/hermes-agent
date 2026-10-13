"""Session & DST behaviour (11.1), including the transition windows where only one country moved."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from gold_agent.config import load_settings
from gold_agent.models import CalendarEvent
from gold_agent.timeutils.risky import risky_flags
from gold_agent.timeutils.sessions import SessionClock, fmt_wib

CLOCK = SessionClock(load_settings().sessions)

SUMMER = date(2026, 10, 13)  # US EDT + UK BST
WINTER = date(2026, 12, 8)  # US EST + UK GMT
UK_ONLY_SWITCHED = date(2026, 10, 27)  # UK back on GMT (25 Oct), US still EDT until 1 Nov
US_ONLY_SWITCHED = date(2027, 3, 16)  # US on EDT (14 Mar), UK still GMT until 28 Mar

# Section 11.1 reference table (WIB), plus the two one-country-switched windows.
TABLE = [
    # moment,          summer,  winter,  UK switched only, US switched only
    ("tokyo_open", "07:00", "07:00", "07:00", "07:00"),
    ("sge_open", "08:00", "08:00", "08:00", "08:00"),
    ("london_open", "14:00", "15:00", "15:00", "15:00"),
    ("lbma_am", "16:30", "17:30", "17:30", "17:30"),
    ("ny_open", "19:00", "20:00", "19:00", "19:00"),
    ("us_data", "19:30", "20:30", "19:30", "19:30"),
    ("lbma_pm", "21:00", "22:00", "22:00", "22:00"),
    ("london_close", "22:00", "23:00", "23:00", "23:00"),
    ("fomc_statement", "01:00", "02:00", "01:00", "01:00"),
    ("daily_close", "04:00", "05:00", "04:00", "04:00"),
]


@pytest.mark.parametrize(("moment", "summer", "winter", "uk_only", "us_only"), TABLE)
def test_session_moments_in_wib_follow_each_countrys_dst(
    moment: str, summer: str, winter: str, uk_only: str, us_only: str
) -> None:
    assert fmt_wib(CLOCK.moment(moment, SUMMER)) == summer
    assert fmt_wib(CLOCK.moment(moment, WINTER)) == winter
    assert fmt_wib(CLOCK.moment(moment, UK_ONLY_SWITCHED)) == uk_only
    assert fmt_wib(CLOCK.moment(moment, US_ONLY_SWITCHED)) == us_only


def test_overlap_is_four_hours_while_only_one_country_has_switched() -> None:
    for day in (UK_ONLY_SWITCHED, US_ONLY_SWITCHED):
        overlap = CLOCK.moment("london_close", day) - CLOCK.moment("ny_open", day)
        assert overlap == timedelta(hours=4)
    assert CLOCK.moment("london_close", SUMMER) - CLOCK.moment("ny_open", SUMMER) == timedelta(
        hours=3
    )


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "session"),
    [
        ("2026-10-13T02:00", "asia"),  # 09:00 WIB
        ("2026-10-13T07:30", "london"),  # 14:30 WIB
        ("2026-10-13T12:30", "overlap"),  # 19:30 WIB, US CPI time
        ("2026-10-13T15:30", "new_york"),  # 22:30 WIB
        ("2026-10-13T21:30", "closed"),  # 04:30 WIB daily break
        ("2026-10-13T22:30", "asia"),  # 05:30 WIB, after the break
        ("2026-10-17T10:00", "closed"),  # Saturday
        ("2026-10-18T21:59", "closed"),  # Sunday 17:59 New York
        ("2026-10-18T22:00", "asia"),  # Sunday 18:00 New York: weekly open
        ("2026-10-27T14:30", "overlap"),  # 21:30 WIB: overlap runs to 23:00 WIB this week
        ("2026-12-08T21:30", "new_york"),  # winter: 16:30 New York, break starts 17:00
    ],
)
def test_session_labels(moment: str, session: str) -> None:
    assert CLOCK.session(utc(moment)) == session


def test_market_status_reasons_and_liquidity() -> None:
    weekend = CLOCK.status(utc("2026-10-17T10:00"))
    assert not weekend.open and weekend.reason == "akhir pekan"
    rollover = CLOCK.status(utc("2026-10-13T21:30"))
    assert not rollover.open and rollover.reason and "rollover" in rollover.reason
    asia = CLOCK.status(utc("2026-10-13T02:00"))
    assert asia.open and asia.liquidity_factor == pytest.approx(0.8)
    thanksgiving = CLOCK.status(utc("2026-11-26T15:00"))
    assert thanksgiving.holidays and thanksgiving.liquidity_factor < 1.0


def test_trading_day_and_weekly_boundaries_track_new_york_dst() -> None:
    assert CLOCK.trading_day_start(utc("2026-10-13T12:30")) == utc("2026-10-12T21:00")
    assert CLOCK.trading_day_start(utc("2026-12-08T12:30")) == utc("2026-12-07T22:00")
    assert CLOCK.next_weekly_open(utc("2026-10-30T23:00")) == utc("2026-11-01T23:00")  # EST
    assert CLOCK.last_weekly_close(utc("2026-10-31T12:00")) == utc("2026-10-30T21:00")  # EDT


def test_risky_flags_cover_releases_rollover_friday_monday_and_holidays() -> None:
    cpi = CalendarEvent(
        id="cpi",
        title="CPI m/m",
        country="USD",
        scheduled_at=utc("2026-10-13T12:30"),
        impact="high",
        source_id="ff",
    )
    near = risky_flags(utc("2026-10-13T12:20"), utc("2026-10-13T12:20"), clock=CLOCK, events=[cpi])
    assert any("CPI" in f for f in near)
    assert not risky_flags(
        utc("2026-10-13T10:00"), utc("2026-10-13T10:30"), clock=CLOCK, events=[cpi]
    )
    assert any(
        "rollover" in f
        for f in risky_flags(
            utc("2026-10-13T20:50"), utc("2026-10-13T20:55"), clock=CLOCK, events=[]
        )
    )
    assert any(
        "Jumat" in f
        for f in risky_flags(
            utc("2026-10-16T19:30"), utc("2026-10-16T20:00"), clock=CLOCK, events=[]
        )
    )
    assert any(
        "gap" in f
        for f in risky_flags(
            utc("2026-10-18T22:10"), utc("2026-10-18T22:20"), clock=CLOCK, events=[]
        )
    )
    assert any(
        "libur AS" in f
        for f in risky_flags(
            utc("2026-11-26T15:00"), utc("2026-11-26T16:00"), clock=CLOCK, events=[]
        )
    )

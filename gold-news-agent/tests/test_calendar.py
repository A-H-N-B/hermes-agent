from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from gold_agent.collectors.http_poller import RetryLater
from gold_agent.core.app import AppContext
from gold_agent.econ_calendar.events import load_events, parse_number, to_unit
from gold_agent.econ_calendar.fomc import (
    FomcMeeting,
    blackout_window,
    in_blackout,
    parse_fomc_calendar,
)
from gold_agent.econ_calendar.forexfactory import parse_ff_week
from gold_agent.econ_calendar.sync import CalendarSync, format_countdown, next_events
from tests.conftest import make_source

CATALOG = load_events()

# Field names as published in the FF weekly JSON (title, country, date, impact, forecast, previous).
FF_WEEK = [
    {
        "title": "CPI m/m",
        "country": "USD",
        "date": "2026-10-13T08:30:00-04:00",
        "impact": "High",
        "forecast": "0.3%",
        "previous": "0.2%",
    },
    {
        "title": "Core CPI m/m",
        "country": "USD",
        "date": "2026-10-13T08:30:00-04:00",
        "impact": "High",
        "forecast": "0.3%",
        "previous": "0.3%",
    },
    {
        "title": "Unemployment Claims",
        "country": "USD",
        "date": "2026-10-15T08:30:00-04:00",
        "impact": "High",
        "forecast": "230K",
        "previous": "225K",
    },
    {
        "title": "CPI y/y",
        "country": "GBP",
        "date": "2026-10-14T02:00:00-04:00",
        "impact": "High",
        "forecast": "2.1%",
        "previous": "2.2%",
    },
    {
        "title": "Bank Holiday",
        "country": "USD",
        "date": "2026-10-12T00:00:00-04:00",
        "impact": "Holiday",
        "forecast": "",
        "previous": "",
    },
]


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_ff_week_parses_times_to_utc_and_maps_us_events() -> None:
    events = parse_ff_week(json.dumps(FF_WEEK).encode(), CATALOG, "ff_calendar_week")
    cpi = events[0]
    assert cpi.scheduled_at == utc("2026-10-13T12:30")
    assert (cpi.impact, cpi.event_key, cpi.forecast, cpi.previous) == (
        "high",
        "us_cpi_mom",
        "0.3%",
        "0.2%",
    )
    assert events[1].event_key == "us_core_cpi_mom"
    assert events[2].event_key == "us_jobless_claims"
    assert events[3].event_key is None  # GBP CPI is not a US event
    assert events[4].all_day and events[4].forecast is None


def test_value_parsing_and_unit_conversion() -> None:
    assert parse_number("150K") == (150.0, "K")
    assert parse_number("-0.1%") == (-0.1, "%")
    assert parse_number("1,234") == (1234.0, "")
    assert parse_number("n/a") is None
    assert to_unit(1.2, "M", "K") == pytest.approx(1200)
    assert to_unit(254000, "", "K") == pytest.approx(254)


def test_countdown_and_next_events() -> None:
    events = parse_ff_week(json.dumps(FF_WEEK).encode(), CATALOG, "ff")
    now = utc("2026-10-13T10:15")
    upcoming = next_events(events, now, limit=2)
    assert [e.title for e, _ in upcoming] == ["CPI m/m", "Core CPI m/m"]
    assert format_countdown(upcoming[0][1]) == "2 jam 15 mnt"
    assert format_countdown(10) == "sekarang"
    assert format_countdown(3 * 86400 + 7200) == "3 hari 2 jam"


async def test_sync_stores_events_keeps_known_actuals_and_honours_rate_limits(
    ctx_factory: Callable[..., AppContext],
) -> None:
    ff = make_source(
        id="ff_calendar_week",
        kind="calendar",
        type="json",
        url="https://nfs.example.com/ff_calendar_thisweek.json",
    )
    context = ctx_factory([ff])
    await context.db.create_all()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 3:
            return httpx.Response(429, headers={"Retry-After": "1200"})
        return httpx.Response(200, content=json.dumps(FF_WEEK).encode())

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sync = CalendarSync(context, client, CATALOG)
            assert await sync.sync_forex_factory() == 5
            stored = await context.calendar.between(
                utc("2026-10-13T00:00"), utc("2026-10-14T00:00")
            )
            assert [e.title for e in stored] == ["CPI m/m", "Core CPI m/m"]
            await context.calendar.set_actual(stored[0].id, "0.4%")
            await sync.sync_forex_factory()  # FF has no actuals: ours must survive the refresh
            again = await context.calendar.between(utc("2026-10-13T00:00"), utc("2026-10-14T00:00"))
            assert again[0].actual == "0.4%"
            with pytest.raises(RetryLater) as excinfo:
                await sync.sync_forex_factory()
            assert excinfo.value.seconds == 1200
            high = await context.calendar.between(
                utc("2026-10-12T00:00"), utc("2026-10-16T00:00"), countries=None, min_impact="high"
            )
            assert {e.country for e in high} == {"USD", "GBP"}
    finally:
        await context.close()


FOMC_PAGE = """
<html><body>
<h4><a id="2026">2026 FOMC Meetings</a></h4>
<div class="fomc-meeting"><div class="fomc-meeting__month"><strong>January</strong></div>
<div class="fomc-meeting__date">27-28</div></div>
<div class="fomc-meeting"><div class="fomc-meeting__month"><strong>March</strong></div>
<div class="fomc-meeting__date">17-18*</div></div>
<div class="fomc-meeting"><div class="fomc-meeting__month"><strong>October</strong></div>
<div class="fomc-meeting__date">27-28</div></div>
<h4><a id="2027">2027 FOMC Meetings</a></h4>
<div class="fomc-meeting"><div class="fomc-meeting__month"><strong>Apr/May</strong></div>
<div class="fomc-meeting__date">30-1</div></div>
</body></html>
"""


def test_fomc_calendar_parsing_and_blackout_window() -> None:
    meetings = parse_fomc_calendar(FOMC_PAGE)
    assert [(m.start, m.end, m.sep) for m in meetings] == [
        (date(2026, 1, 27), date(2026, 1, 28), False),
        (date(2026, 3, 17), date(2026, 3, 18), True),
        (date(2026, 10, 27), date(2026, 10, 28), False),
        (date(2027, 4, 30), date(2027, 5, 1), False),
    ]
    october = FomcMeeting(date(2026, 10, 27), date(2026, 10, 28), False)
    start, end = blackout_window(october)
    assert start == datetime(2026, 10, 17, 4, 0, tzinfo=UTC)  # Sat 17 Oct 00:00 EDT
    assert end == datetime(2026, 10, 30, 4, 0, tzinfo=UTC)  # end of Thu 29 Oct EDT
    assert in_blackout(utc("2026-10-20T15:00"), [october]) == october
    assert in_blackout(utc("2026-10-30T12:00"), [october]) is None
    assert in_blackout(start - timedelta(seconds=1), [october]) is None

"""Fast-path release parsing (7.5): many real-world headline shapes, units, country guards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gold_agent.econ_calendar.events import EventSpec, load_events
from gold_agent.econ_calendar.playbook import build_playbook, match_bucket
from gold_agent.econ_calendar.release_parser import ReleaseParser, bucket_for
from gold_agent.models import CalendarEvent

CATALOG = load_events()
PARSER = ReleaseParser(CATALOG, window_minutes=30)
NOW = datetime(2026, 10, 13, 12, 30, 4, tzinfo=UTC)

# headline, event key, actual, forecast, previous, revised
CASES: list[tuple[str, str, float, float | None, float | None, float | None]] = [
    ("US CPI MoM 0.4% vs 0.3% exp; prior 0.2%", "us_cpi_mom", 0.4, 0.3, 0.2, None),
    ("*US CPI M/M 0.4%, EST. 0.3%", "us_cpi_mom", 0.4, 0.3, None, None),
    ("US CPI (MoM) Sep: 0.4% (est 0.3%; prev 0.2%)", "us_cpi_mom", 0.4, 0.3, 0.2, None),
    ("US CPI MoM (Sep 2026): 0.4% vs 0.3% exp", "us_cpi_mom", 0.4, 0.3, None, None),
    (
        "US Core CPI MoM Actual 0.3% (Forecast 0.3%, Previous 0.3%)",
        "us_core_cpi_mom",
        0.3,
        0.3,
        0.3,
        None,
    ),
    ("US CPI YoY 2.4% vs 2.3% expected", "us_cpi_yoy", 2.4, 2.3, None, None),
    (
        "US Initial Jobless Claims Actual 227K (Forecast 230K, Previous 233K)",
        "us_jobless_claims",
        227,
        230,
        233,
        None,
    ),
    ("US Initial Jobless Claims 225K vs. 230K expected", "us_jobless_claims", 225, 230, None, None),
    (
        "U.S. Nonfarm Payrolls 254K vs. Est. 150K (Prev. 159K, Rev. 142K)",
        "us_nfp",
        254,
        150,
        159,
        142,
    ),
    ("US Nonfarm Payrolls 254,000 vs 150,000 expected", "us_nfp", 254, 150, None, None),
    ("US ADP Employment Change 143K vs 120K exp", "us_adp", 143, 120, None, None),
    (
        "U.S. Unemployment Rate 4.1% (exp 4.2%, prior 4.2%)",
        "us_unemployment_rate",
        4.1,
        4.2,
        4.2,
        None,
    ),
    (
        "USD Core PCE Price Index m/m: 0.2% (forecast 0.3%; previous 0.1%)",
        "us_core_pce_mom",
        0.2,
        0.3,
        0.1,
        None,
    ),
    (
        "US ISM Manufacturing PMI 48.7 vs 49.5 expected",
        "us_ism_manufacturing",
        48.7,
        49.5,
        None,
        None,
    ),
    (
        "US Retail Sales MoM Actual 0.4% (Forecast 0.3%, Previous 0.6%)",
        "us_retail_sales_mom",
        0.4,
        0.3,
        0.6,
        None,
    ),
    ("US GDP Q3 Advance 2.8% vs 3.0% consensus", "us_gdp_qoq", 2.8, 3.0, None, None),
    ("US JOLTS Job Openings 7.74M vs 7.60M exp", "us_jolts", 7.74, 7.60, None, None),
    ("US CPI MoM −0.1% vs +0.1% exp", "us_cpi_mom", -0.1, 0.1, None, None),
]


@pytest.mark.parametrize(("headline", "key", "actual", "forecast", "previous", "revised"), CASES)
def test_release_headlines_parse_into_numbers(
    headline: str,
    key: str,
    actual: float,
    forecast: float | None,
    previous: float | None,
    revised: float | None,
) -> None:
    reading = PARSER.parse(headline, now=NOW, first_seen_at=NOW)
    assert reading is not None, headline
    assert reading.event_key == key
    assert reading.actual == pytest.approx(actual)
    assert reading.forecast == (pytest.approx(forecast) if forecast is not None else None)
    assert reading.previous == (pytest.approx(previous) if previous is not None else None)
    assert reading.previous_revised == (pytest.approx(revised) if revised is not None else None)


@pytest.mark.parametrize(
    "headline",
    [
        "UK CPI y/y 2.0% vs 2.1% exp",
        "Canada Unemployment Rate 6.5% vs 6.6% exp",
        "Eurozone CPI y/y 2.2%",
        "Japan CPI 2.5%",
        "Fed's Waller says rate cuts still appropriate",
        "Gold rises 1% as dollar slides",
        "Atlanta Fed GDPNow model estimate 3.1%",
    ],
)
def test_non_us_data_and_non_releases_are_ignored(headline: str) -> None:
    assert PARSER.parse(headline, now=NOW, first_seen_at=NOW) is None


def cpi_event(**extra: object) -> CalendarEvent:
    return CalendarEvent.model_validate(
        {
            "id": "cpi",
            "title": "CPI m/m",
            "country": "USD",
            "scheduled_at": datetime(2026, 10, 13, 12, 30, tzinfo=UTC),
            "impact": "high",
            "forecast": "0.3%",
            "previous": "0.2%",
            "event_key": "us_cpi_mom",
            "source_id": "ff",
        }
        | extra
    )


def test_headline_without_country_needs_a_scheduled_us_release() -> None:
    headline = "CPI MoM 0.4% vs 0.3% exp"
    assert PARSER.parse(headline, now=NOW, first_seen_at=NOW) is None
    reading = PARSER.parse(headline, now=NOW, first_seen_at=NOW, calendar=[cpi_event()])
    assert reading is not None and reading.event_key == "us_cpi_mom"
    late = NOW + timedelta(hours=2)
    assert PARSER.parse(headline, now=late, first_seen_at=late, calendar=[cpi_event()]) is None


def test_missing_forecast_comes_from_the_calendar() -> None:
    reading = PARSER.parse("US CPI MoM 0.5%", now=NOW, first_seen_at=NOW, calendar=[cpi_event()])
    assert reading is not None
    assert reading.forecast == pytest.approx(0.3) and reading.forecast_source == "calendar"
    nothing = PARSER.parse("US CPI MoM 0.5%", now=NOW, first_seen_at=NOW)
    assert nothing is not None and nothing.forecast is None and nothing.bucket == "unknown"


def test_surprise_direction_respects_hawkish_and_dovish_events() -> None:
    hot_cpi = PARSER.parse("US CPI MoM 0.5% vs 0.3% exp", now=NOW, first_seen_at=NOW)
    assert hot_cpi is not None
    assert hot_cpi.z == pytest.approx(2.0) and hot_cpi.z_basis == "prior"
    assert (hot_cpi.bucket, hot_cpi.direction_gold) == ("strong_bearish", "bearish")
    weak_jobs = PARSER.parse("US Unemployment Rate 4.4% vs 4.2% exp", now=NOW, first_seen_at=NOW)
    assert weak_jobs is not None and weak_jobs.hawkish_z == pytest.approx(-2.0)
    assert (weak_jobs.bucket, weak_jobs.direction_gold) == ("strong_bullish", "bullish")
    inline = PARSER.parse("US CPI MoM 0.3% vs 0.3% exp", now=NOW, first_seen_at=NOW)
    assert inline is not None and inline.direction_gold == "neutral"


def test_measured_surprise_std_replaces_the_prior() -> None:
    def calibrated(spec: EventSpec) -> tuple[float, str]:
        return 0.05, "calibrated"

    reading = PARSER.parse(
        "US CPI MoM 0.4% vs 0.3% exp",
        now=NOW,
        first_seen_at=NOW,
        std_provider=calibrated,  # type: ignore[arg-type]
    )
    assert (
        reading is not None and reading.z == pytest.approx(2.0) and reading.z_basis == "calibrated"
    )


def test_bucket_boundaries_follow_section_11_4() -> None:
    assert [bucket_for(z) for z in (2.0, 1.5, 1.0, 0.49, -0.49, -1.0, -1.5, -3.0)] == [
        "strong_bearish",
        "strong_bearish",
        "mild_bearish",
        "neutral",
        "neutral",
        "mild_bullish",
        "strong_bullish",
        "strong_bullish",
    ]


def test_playbook_translates_buckets_into_actual_thresholds() -> None:
    spec = CATALOG.by_key("us_cpi_mom")
    assert spec is not None
    playbook = build_playbook(cpi_event(), spec, surprise_std=0.1, z_basis="prior", now=NOW)
    by_name = {b.name: b for b in playbook.buckets}
    assert by_name["strong_bearish"].actual_min == pytest.approx(0.45)
    assert by_name["mild_bearish"].actual_min == pytest.approx(0.35)
    assert by_name["neutral"].actual_min == pytest.approx(0.25)
    assert by_name["strong_bullish"].actual_max == pytest.approx(0.15)
    assert any("prior" in note for note in playbook.notes)
    reading = PARSER.parse("US CPI MoM 0.5% vs 0.3% exp", now=NOW, first_seen_at=NOW)
    assert reading is not None
    matched = match_bucket(playbook, reading)
    assert matched is not None and matched.name == "strong_bearish"


def test_playbook_for_a_dovish_event_inverts_the_thresholds() -> None:
    spec = CATALOG.by_key("us_jobless_claims")
    assert spec is not None
    claims = cpi_event(
        id="claims", title="Unemployment Claims", forecast="230K", event_key=spec.key
    )
    playbook = build_playbook(claims, spec, surprise_std=12, z_basis="prior", now=NOW)
    by_name = {b.name: b for b in playbook.buckets}
    # More claims than expected = weaker labour market = bullish gold.
    assert by_name["strong_bullish"].actual_min == pytest.approx(248)
    assert by_name["strong_bearish"].actual_max == pytest.approx(212)

"""Daily fundamentals (fundamentals/): the official files are read tolerantly, only verified
sources are fetched, and each driver pulls the day's bias the way the literature says it
should (falling real yields and a weaker dollar support gold; crowded longs warn)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from gold_agent.config import SourceRegistry, SourceStatus, load_sources
from gold_agent.core.app import AppContext
from gold_agent.fundamentals.fetch import FundamentalsFetcher
from gold_agent.fundamentals.parsers import parse_cot, parse_curve, parse_gld
from gold_agent.fundamentals.snapshot import Inputs, build_snapshot, cot_factor
from gold_agent.models import ReleaseReading
from tests.builders import analysis

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)

NOMINAL_CSV = (
    'Date,"1 Mo","1.5 Month","2 Mo","3 Mo","6 Mo","1 Yr","2 Yr","5 Yr","10 Yr","30 Yr"\n'
    + "".join(
        f"10/{21 - i:02d}/2026,4.10,4.08,4.05,4.01,3.90,3.75,{3.40 + 0.03 * i:.2f},3.55,"
        f"{3.95 + 0.02 * i:.2f},4.55\n"
        for i in range(8)
    )
)
REAL_CSV = 'Date,"5 YR","7 YR","10 YR","20 YR","30 YR"\n' + "".join(
    f"10/{21 - i:02d}/2026,1.45,1.60,{1.70 + 0.04 * i:.2f},2.10,2.20\n" for i in range(8)
)
GLD_CSV = (
    '"SPDR® Gold Trust"\n"Data as of 21-Oct-2026"\n\n'
    "Date, GLD Close, LBMA Gold Price, NAV, Total Net Asset Value Ounces in the Trust as at "
    "4.15 p.m. NYT, Total Net Asset Value Tonnes in the Trust as at 4.15 p.m. NYT\n"
    + "".join(
        f"{d:%d-%b-%Y}, 380.1, 4100, 380.0, 30000000, {900 + 1.5 * i:.2f}\n"
        for i, d in enumerate(date(2026, 9, 20) + timedelta(days=k) for k in range(30))
    )
    + "25-Oct-2026, HOLIDAY, HOLIDAY, HOLIDAY, HOLIDAY, HOLIDAY\n"
)


def cot_rows(shares: list[float]) -> list[dict[str, Any]]:
    start = date(2023, 10, 24)
    return [
        {
            "report_date_as_yyyy_mm_dd": f"{start + timedelta(weeks=i)}T00:00:00.000",
            "open_interest_all": "500000",
            "m_money_positions_long_all": str(int(150000 + share * 500000)),
            "m_money_positions_short_all": "150000",
        }
        for i, share in enumerate(shares)
    ]


def test_treasury_files_are_read_by_tenor_newest_rows_included() -> None:
    nominal, real = parse_curve(NOMINAL_CSV), parse_curve(REAL_CSV)
    assert nominal[date(2026, 10, 21)]["2Y"] == 3.40 and nominal[date(2026, 10, 21)]["10Y"] == 3.95
    assert nominal[date(2026, 10, 21)]["1.5M"] == 4.08
    assert real[date(2026, 10, 14)]["10Y"] == pytest.approx(1.98)
    assert parse_curve("<html>maintenance</html>") == {}


def test_cot_reads_managed_money_or_the_legacy_report() -> None:
    weeks = parse_cot(cot_rows([0.2, 0.25]))
    assert [w.net_share for w in weeks] == [0.2, 0.25] and weeks[0].day == date(2023, 10, 24)
    legacy = parse_cot(
        [
            {
                "report_date_as_yyyy_mm_dd": "2026-10-13T00:00:00.000",
                "open_interest_all": "400000",
                "noncomm_positions_long_all": "250000",
                "noncomm_positions_short_all": "50000",
            }
        ]
    )
    assert legacy[0].net == 200000


def test_gld_skips_the_notes_and_holidays_above_and_inside_the_table() -> None:
    points = parse_gld(GLD_CSV)
    assert points[0] == (date(2026, 9, 20), 900.0) and len(points) == 30
    assert parse_gld("nothing,here\n1,2\n") == []


def test_crowded_speculators_warn_and_in_between_their_direction_counts() -> None:
    calm = [0.10 + 0.001 * (i % 7) for i in range(150)]
    crowded = cot_factor(parse_cot(cot_rows([*calm, 0.30])))
    assert crowded is not None and crowded.effect == -0.5 and "sangat long" in crowded.note
    squeezed = cot_factor(parse_cot(cot_rows([*calm, 0.01])))
    assert squeezed is not None and squeezed.effect == 0.5
    spread = [0.05 + 0.002 * i for i in range(150)]  # 5% .. 35% of open interest
    adding = cot_factor(parse_cot(cot_rows([*spread, 0.15, 0.18])))
    assert adding is not None and 0 < adding.effect < 0.5 and "menambah long" in adding.note


def test_falling_real_yields_and_a_weaker_dollar_make_a_bullish_day() -> None:
    hawkish = ReleaseReading(
        event_key="us_cpi_mom",
        event_name="CPI m/m",
        headline="US CPI 0.5% vs 0.3%",
        actual=0.5,
        unit="%",
        hawkish_z=2.0,
        first_seen_at=NOW - timedelta(days=20),
        parsed_at=NOW - timedelta(days=20),
    )
    inputs = Inputs(
        nominal=parse_curve(NOMINAL_CSV),  # 2Y and 10Y fell over the week
        real=parse_curve(REAL_CSV),  # real 10Y fell 20 bp
        dollar_5d_pct=-0.8,
        dollar_source="sintetis 3/6 pasangan",
        releases=[hawkish],  # an old hawkish surprise: small, decayed
    )
    snapshot = build_snapshot(inputs, NOW)
    by_key = {f.key: f for f in snapshot.factors}
    assert (
        by_key["real10y"].effect > 0.7 and by_key["real10y"].change == "-20 bp dalam 5 hari bursa"
    )
    assert by_key["us2y"].effect > 0.5 and by_key["dollar"].effect > 0.5
    assert by_key["surprise"].effect < 0  # beats on US data still lean against gold
    assert snapshot.bias is not None and snapshot.bias > 0.4
    assert snapshot.summary.startswith("Fundamental harian condong NAIK")
    assert "Posisi spekulan (CFTC, managed money)" in snapshot.missing
    empty = build_snapshot(Inputs(), NOW)
    assert empty.bias is None and empty.summary == "data fundamental belum tersedia"


def test_geopolitical_news_heat_supports_gold_only_when_it_rises() -> None:
    def story(hours_ago: float, impact: int) -> Any:
        return analysis(
            first_seen=NOW - timedelta(hours=hours_ago), category="geopolitics", impact_score=impact
        )

    quiet = [story(24 * d + 5, 3) for d in range(1, 14)]
    flare = build_snapshot(Inputs(analyses=[*quiet, story(2, 5), story(3, 5), story(4, 4)]), NOW)
    calm = build_snapshot(Inputs(analyses=quiet), NOW)
    heat = {f.key: f for f in flare.factors}["geopolitics"]
    assert heat.effect > 0.5 and "×" in (heat.change or "")
    assert {f.key: f for f in calm.factors}["geopolitics"].effect == 0.0
    # Alone it is a minor driver: the missing yields, dollar and positioning count as neutral,
    # not as if they agreed with it.
    assert flare.bias == pytest.approx(heat.effect * 0.03, abs=1e-3)
    assert flare.summary.startswith("Fundamental harian condong NETRAL")


def verified(*ids: str) -> SourceRegistry:
    registry = load_sources(local=False)
    return SourceRegistry(
        sources=tuple(
            s.model_copy(update={"status": SourceStatus.VERIFIED}) if s.id in ids else s
            for s in registry.sources
        )
    )


def transport(log: list[str]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        log.append(url)
        if "daily_treasury_real_yield_curve" in url:
            return httpx.Response(200, text=REAL_CSV)
        if "daily_treasury_yield_curve" in url:
            return httpx.Response(200, text=NOMINAL_CSV if "/2026/" in url else "Date\n")
        if "cftc" in url:
            return httpx.Response(200, json=cot_rows([0.1, 0.12]))
        return httpx.Response(404)

    return httpx.MockTransport(handle)


async def test_only_verified_sources_are_fetched_each_at_its_own_pace() -> None:
    fetcher = FundamentalsFetcher(verified("treasury_yield_curve", "cftc_cot_gold"), {})
    calls: list[str] = []
    async with httpx.AsyncClient(transport=transport(calls)) as client:
        await fetcher.refresh(client, NOW)
        assert any("/2026/all?type=daily_treasury_yield_curve" in url for url in calls)
        assert not any("real_yield" in url or "spdr" in url for url in calls)
        assert fetcher.inputs.nominal and len(fetcher.inputs.cot) == 2
        calls.clear()
        await fetcher.refresh(client, NOW + timedelta(hours=1))
        assert calls == []  # six-hourly sources are not fetched again within the hour
    january = FundamentalsFetcher(verified("treasury_yield_curve"), {})
    calls.clear()
    async with httpx.AsyncClient(transport=transport(calls)) as client:
        await january.refresh(client, datetime(2027, 1, 4, 6, tzinfo=UTC))
    # The new year's file is still short: last year's is read as well.
    assert any("/2027/" in url for url in calls) and any("/2026/" in url for url in calls)
    assert len(january.inputs.nominal) == 8


async def test_the_market_service_builds_the_bias_and_yields_without_fred(
    ctx_factory: Callable[..., AppContext], clock: Any
) -> None:
    from gold_agent.fundamentals.snapshot import KV_FUNDAMENTALS
    from gold_agent.services.market import MarketService

    context = ctx_factory()
    await context.db.create_all()
    try:
        clock.set(NOW)
        service = MarketService(context)
        service.fetcher = FundamentalsFetcher(
            verified("treasury_yield_curve", "treasury_real_yield_curve"), {}
        )
        async with httpx.AsyncClient(transport=transport([])) as client:
            snapshot = await service.refresh_fundamentals(client)
        assert snapshot.bias is not None and snapshot.bias > 0
        stored = await context.ops.kv_get(KV_FUNDAMENTALS)
        assert stored is not None and stored["bias"] == snapshot.bias
        assert service.yields is not None and service.yields.us10y == 3.95
        assert service.yields.real10y_change_1d == pytest.approx(-0.04)
        assert service.context(NOW).fundamental_bias == snapshot.bias
    finally:
        await context.close()

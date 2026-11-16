"""Event playbooks (11.4): built at T-60, rebuilt when the forecast appears, sent once at T-30."""

from __future__ import annotations

from datetime import timedelta

from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_PLAYBOOKS
from gold_agent.core.clock import ManualClock
from gold_agent.econ_calendar.calibration import MIN_CALIBRATION_SAMPLES, SurpriseCalibration
from gold_agent.econ_calendar.events import load_events
from gold_agent.econ_calendar.playbook_job import PlaybookScheduler
from gold_agent.models import ReleaseReading
from tests.builders import T0, event


async def published(ctx: AppContext) -> list[str]:
    messages = await ctx.bus.read(STREAM_PLAYBOOKS, "test", "t", count=50, block_ms=0)
    return [m.data["event_title"] for m in messages]


async def test_playbook_is_built_early_and_sent_once_at_t_minus_30(
    ctx: AppContext, clock: ManualClock
) -> None:
    catalog = load_events()
    calibration = SurpriseCalibration(catalog, ctx.news)
    scheduler = PlaybookScheduler(ctx, catalog, calibration)
    cpi = event(at=T0, forecast=None)
    speech = event("Fed Chair Speaks", at=T0, key=None, forecast=None)
    medium = event("Core PPI m/m", at=T0, impact="medium", key="us_ppi_mom")
    await ctx.calendar.upsert_events([cpi, speech, medium], T0 - timedelta(days=1))

    clock.set(T0 - timedelta(minutes=70))
    assert await scheduler.tick() == []
    assert await ctx.calendar.playbook_for(cpi.id) is None  # outside the T-60 window

    clock.set(T0 - timedelta(minutes=55))
    assert await scheduler.tick() == []
    built = await ctx.calendar.playbook_for(cpi.id)
    assert built is not None and built.forecast is None and built.sent_at is None
    assert await ctx.calendar.playbook_for(speech.id) is None  # no spec: nothing to pre-compute
    assert await ctx.calendar.playbook_for(medium.id) is None

    # The forecast lands in the calendar: the playbook is rebuilt with real thresholds.
    await ctx.calendar.upsert_events([cpi.model_copy(update={"forecast": "0.3%"})], clock.now())
    clock.set(T0 - timedelta(minutes=40))
    await scheduler.tick()
    rebuilt = await ctx.calendar.playbook_for(cpi.id)
    assert rebuilt is not None and rebuilt.id == built.id and rebuilt.forecast == 0.3

    clock.set(T0 - timedelta(minutes=29))
    sent = await scheduler.tick()
    assert [p.event_title for p in sent] == ["CPI m/m"]
    clock.set(T0 - timedelta(minutes=10))
    assert await scheduler.tick() == []
    assert await published(ctx) == ["CPI m/m"]
    final = await ctx.calendar.playbook_for(cpi.id)
    assert final is not None and final.sent_at == T0 - timedelta(minutes=29)


async def test_recorded_releases_replace_the_prior_surprise_std(ctx: AppContext) -> None:
    catalog = load_events()
    spec = catalog.by_key("us_cpi_mom")
    assert spec is not None
    calibration = SurpriseCalibration(catalog, ctx.news)
    await calibration.refresh()
    assert calibration.std_for(spec) == (spec.surprise_std, "prior")
    for n in range(MIN_CALIBRATION_SAMPLES):
        surprise = 0.2 if n % 2 else -0.2
        await ctx.news.save_release(
            ReleaseReading(
                event_key=spec.key,
                event_name=spec.name,
                headline=f"US CPI {n}",
                actual=0.3 + surprise,
                forecast=0.3,
                unit="%",
                surprise=surprise,
                first_seen_at=T0 - timedelta(days=30 * n),
                parsed_at=T0 - timedelta(days=30 * n),
            )
        )
    await calibration.refresh()
    std, basis = calibration.std_for(spec)
    assert basis == "calibrated" and round(std, 6) == 0.2

"""Replay edge cases: a failed start-up ends instead of hanging, inputs that leave the bus idle
cannot shift later timestamps, and the plan carries what production would have known then
(full warm-up history, FOMC calendar, earlier releases) but nothing it could not have known."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from gold_agent.core.app import AppContext
from gold_agent.econ_calendar.sync import FOMC_KV_KEY
from gold_agent.models import CalendarEvent, ReleaseReading, Tick
from gold_agent.ops.replay import day_bounds, load_plan, run_replay
from gold_agent.services.market import MarketService
from tests.conftest import make_source
from tests.test_replay_bridge import DAY, WINDOW, history, item, seed_recording


async def test_a_replay_whose_services_never_come_up_fails_instead_of_hanging(
    ctx_factory: Callable[..., AppContext], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stuck(_self: MarketService, _adapter: object) -> None:
        await asyncio.Event().wait()  # history never finishes loading

    monkeypatch.setattr("gold_agent.ops.replay.STARTUP_SECONDS", 0.5)
    monkeypatch.setattr("gold_agent.ops.replay.SHUTDOWN_SECONDS", 1.0)  # then it is cancelled
    monkeypatch.setattr(MarketService, "load_history", stuck)
    source = ctx_factory([make_source()])
    await source.db.create_all()
    await seed_recording(source)
    replay = asyncio.create_task(
        run_replay(source, DAY, database_url=f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    )
    done, _ = await asyncio.wait({replay}, timeout=15)
    await source.close()
    assert replay in done, "the replay hung after its start-up timeout"
    assert isinstance(replay.exception(), TimeoutError)


async def test_an_input_that_leaves_the_bus_idle_cannot_delay_the_next_one(
    ctx_factory: Callable[..., AppContext], tmp_path: Path
) -> None:
    source = ctx_factory([make_source()])
    await source.db.create_all()
    await seed_recording(source)
    release_at = day_bounds(DAY, WINDOW[0])[0] + timedelta(minutes=5, seconds=2)
    first = item(10, "Gold steady ahead of US data", release_at - timedelta(milliseconds=200))
    repeat = first.model_copy(  # same story, same source: dedup publishes nothing
        update={"id": "feed:11", "first_seen_at": release_at - timedelta(milliseconds=100)}
    )
    await source.news.save_item(first)
    await source.news.save_item(repeat)
    target = f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"
    await run_replay(
        source, DAY, database_url=target, speed=1_000_000, start_time=WINDOW[0], end_time=WINDOW[1]
    )
    await source.close()
    scratch = ctx_factory([make_source()], database_url=target)
    flashes = [n for n in await scratch.notify.recent(limit=100) if n.kind == "flash"]
    await scratch.close()
    assert [n.created_at for n in flashes] == [release_at]  # exactly when the CPI arrived


async def test_the_plan_carries_what_production_knew_at_the_start_and_nothing_later(
    ctx: AppContext,
) -> None:
    start = day_bounds(DAY, *WINDOW)[0]
    h1 = history("H1", start, 1600)  # 66 days: far more than any fixed warm-up window
    await ctx.market.upsert_candles([*h1, *history("H1", start + timedelta(hours=3), 2)])
    tick = Tick(ts=start, bid=2640.0, ask=2640.2, source="mt5", received_at=start)
    await ctx.market.insert_ticks([tick])  # the recording's price source picks the history
    await ctx.ops.kv_set(
        FOMC_KV_KEY,
        {"meetings": [{"start": "2026-10-27", "end": "2026-10-28", "sep": False}]},
        start,
    )

    def reading(at_minutes: int) -> ReleaseReading:
        at = start + timedelta(minutes=at_minutes)
        return ReleaseReading(
            event_key="us_cpi_mom",
            event_name="CPI m/m",
            headline="CPI",
            actual=0.1,
            forecast=0.3,
            unit="%",
            surprise=-0.2,
            first_seen_at=at,
            parsed_at=at,
        )

    await ctx.news.save_release(reading(-60 * 24 * 30))
    await ctx.news.save_release(reading(5))  # inside the window: the replay re-parses it
    cpi = CalendarEvent(
        id="cpi",
        title="CPI m/m",
        country="USD",
        impact="high",
        scheduled_at=start + timedelta(minutes=5),
        forecast="0.3%",
        actual="0.1%",
        source_id="ff",
    )
    older = cpi.model_copy(update={"id": "ppi", "scheduled_at": start - timedelta(hours=2)})
    await ctx.calendar.upsert_events([cpi, older], start)

    plan = await load_plan(ctx, DAY, start_time=WINDOW[0], end_time=WINDOW[1])
    bars = ctx.settings.market.history_bars["H1"]
    assert [c.open_time for c in plan.history["H1"]] == [c.open_time for c in h1[-bars:]]
    assert plan.fomc == {"meetings": [{"start": "2026-10-27", "end": "2026-10-28", "sep": False}]}
    assert [r.first_seen_at for r in plan.releases] == [start - timedelta(days=30)]
    actuals = {e.id: e.actual for e in plan.events}
    assert actuals == {"cpi": None, "ppi": "0.1%"}  # not yet released at the start

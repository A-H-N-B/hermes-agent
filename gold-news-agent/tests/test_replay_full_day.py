"""Mandatory test (section 21): replay one whole recorded day end to end. About two minutes of
processing, so it is marked slow: `uv run pytest -m slow`."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest

from gold_agent.core.app import AppContext
from gold_agent.models import Tick
from gold_agent.ops.replay import day_bounds, run_replay
from gold_agent.timeutils.sessions import WIB
from tests.conftest import make_source
from tests.test_replay_bridge import TF_MINUTES, history, item

DAY = date(2026, 10, 13)
HEADLINES = [
    "US CPI m/m 0.1% vs 0.3% expected",
    "Fed's Powell says policy is well positioned",
    "Missile strike on oil terminal lifts safe-haven gold",
    "Dollar index climbs to two-week high",
    "China central bank adds gold to reserves for 18th month",
    "Treasury yields fall after weak data",
    "Local football club signs new striker",
    "US jobless claims 210K vs 225K expected",
]


@pytest.mark.slow
async def test_a_whole_recorded_day_replays_end_to_end(
    ctx_factory: Callable[..., AppContext], tmp_path: Path
) -> None:
    source = ctx_factory([make_source()])
    await source.db.create_all()
    start, end = day_bounds(DAY)
    for tf, minutes in TF_MINUTES.items():
        await source.market.upsert_candles(history(tf, start, min(240, 2400 // minutes)))
    for n in range(40):  # a headline every 36 minutes, the last one at 23:31 WIB
        at = start + timedelta(minutes=36 * n + 7)
        await source.news.save_item(item(n, f"{HEADLINES[n % len(HEADLINES)]} ({n})", at))
    ticks: list[Tick] = []
    at, i = start, 0
    while at < end:  # every 10 s through Asia, London, New York and the daily break
        price = 2640 + 8 * math.sin(i / 900) + 0.3 * math.sin(i / 7)
        ticks.append(Tick(ts=at, bid=price, ask=price + 0.25, source="mt5", received_at=at))
        at, i = at + timedelta(seconds=10), i + 1
    await source.market.insert_ticks(ticks)

    target = f"sqlite+aiosqlite:///{tmp_path / 'day.db'}"
    summary = await run_replay(source, DAY, database_url=target, speed=1_000_000)
    await source.close()
    assert summary.items == 40 and summary.ticks == len(ticks) == 8640
    assert summary.clusters == 40
    assert summary.releases >= 1 and summary.notifications.get("flash", 0) >= 1
    scratch = ctx_factory([make_source()], database_url=target)
    analyses = await scratch.news.analyses_between(start, end)
    contexts = await scratch.market.contexts_between(start, end)
    await scratch.close()
    assert max(a.first_seen_at for a in analyses).astimezone(WIB).hour == 23  # to the very end
    assert len({c.ts.astimezone(WIB).hour for c in contexts}) == 24  # every hour, break included

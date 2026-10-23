"""Replay mode end to end (recorded news + ticks through the real pipeline, faster than real time)
and the Windows MT5 bridge round trip over the bus."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from gold_agent.core.app import AppContext
from gold_agent.core.bus import MemoryBus
from gold_agent.core.clock import ManualClock
from gold_agent.market.adapters.bridge import BridgeTickAdapter
from gold_agent.market.adapters.mt5 import Mt5Adapter
from gold_agent.market.bridge_publisher import BridgePublisher
from gold_agent.models import Candle, NewsItem, Tick
from gold_agent.models.common import Timeframe
from gold_agent.ops.replay import ReplaySummary, day_bounds, run_replay
from gold_agent.processing.normalizer import title_hash
from tests.conftest import make_source
from tests.test_market_feeds import FakeMt5

DAY = date(2026, 10, 13)  # Tuesday; 19:30 WIB = 08:30 New York (US CPI time)
WINDOW = (time(19, 25), time(19, 45))
TICK_EVERY = timedelta(seconds=10)  # below stale_tick_seconds, like a real 1/s recording
TF_MINUTES: dict[Timeframe, int] = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60}


def item(n: int, title: str, at: datetime, source: str = "feed") -> NewsItem:
    return NewsItem(
        id=f"{source}:{n}",
        source_id=source,
        title=title,
        first_seen_at=at,
        published_at=at - timedelta(seconds=5),
        raw_hash=title_hash(title),
        tier=1,
        reliability=0.9,
    )


def history(tf: Timeframe, end: datetime, bars: int) -> list[Candle]:
    """A slow climb that ends where the recorded ticks start (2640), like real history."""
    step = timedelta(minutes=TF_MINUTES[tf])
    start = end - step * bars
    base = 2640 - bars * 0.05
    return [
        Candle(
            source="mt5",
            tf=tf,
            open_time=start + step * i,
            close_time=start + step * (i + 1),
            open=base + i * 0.05,
            high=base + i * 0.05 + 1.5,
            low=base + i * 0.05 - 1.5,
            close=base + (i + 1) * 0.05,
        )
        for i in range(bars)
    ]


async def seed_recording(ctx: AppContext) -> None:
    start, end = day_bounds(DAY, *WINDOW)
    release_at = day_bounds(DAY, time(19, 30))[0] + timedelta(seconds=2)
    await ctx.news.save_item(item(1, "US CPI m/m 0.1% vs 0.3% expected", release_at))
    await ctx.news.save_item(
        item(
            2,
            "Missile strike on oil terminal lifts safe-haven gold",
            day_bounds(DAY, time(19, 40))[0],
        )
    )
    await ctx.news.save_item(
        item(3, "Local football club signs new striker", start + timedelta(seconds=30))
    )
    for tf, minutes in TF_MINUTES.items():
        await ctx.market.upsert_candles(history(tf, start, min(240, 2400 // minutes)))
    ticks: list[Tick] = []
    price, at = 2640.0, start
    while at < end:  # flat until the release, then a rally
        price += 0.15 if at > release_at else 0.01
        ticks.append(Tick(ts=at, bid=price, ask=price + 0.2, source="mt5", received_at=at))
        at += TICK_EVERY
    await ctx.market.insert_ticks(ticks)


async def replay_once(
    ctx_factory: Callable[..., AppContext], tmp_path: Path, name: str, speed: float
) -> tuple[ReplaySummary, list[tuple[datetime, str, str]]]:
    """Seed a recording, replay it into its own scratch DB, return the summary and the
    notification log the replay recorded."""
    source = ctx_factory([make_source()], database_url=f"sqlite+aiosqlite:///{tmp_path / name}")
    await source.db.create_all()
    await seed_recording(source)
    target = f"sqlite+aiosqlite:///{tmp_path / f'replay-{name}'}"
    summary = await asyncio.wait_for(
        run_replay(
            source, DAY, database_url=target, speed=speed, start_time=WINDOW[0], end_time=WINDOW[1]
        ),
        timeout=60,
    )
    scratch = ctx_factory([make_source()], database_url=target)
    log = sorted((n.created_at, n.kind, n.text) for n in await scratch.notify.recent(limit=1000))
    await scratch.close()
    await source.close()
    return summary, log


async def test_replay_runs_the_recorded_window_through_the_pipeline(
    ctx_factory: Callable[..., AppContext], tmp_path: Path
) -> None:
    summary, log = await replay_once(ctx_factory, tmp_path, "a.db", speed=100_000)
    assert summary.items == 3 and summary.ticks == 120
    assert summary.clusters == 3
    assert summary.relevant >= 1
    assert summary.releases == 1  # the CPI headline took the fast path, no LLM involved
    assert summary.notifications.get("flash") == 1  # fresh: processing took no replay time
    assert [s.split()[0] for s in summary.signals] == ["BUY"]  # soft CPI + rally
    (outcome,) = summary.outcomes  # the paper tracker filled it and the rally reached TP2
    assert outcome.startswith("tp2 +")
    assert not [kind for _, kind, _ in log if kind == "health"]  # no spurious stale price
    text = summary.render()
    assert text.startswith("Replay 2026-10-13 19:25 – 2026-10-13 19:45 WIB")
    assert "tick diputar: 120" in text


async def test_replay_results_do_not_depend_on_speed(
    ctx_factory: Callable[..., AppContext], tmp_path: Path
) -> None:
    slow, slow_log = await replay_once(ctx_factory, tmp_path, "slow.db", speed=600)
    fast, fast_log = await replay_once(ctx_factory, tmp_path, "fast.db", speed=100_000)
    assert slow_log and slow_log == fast_log  # same notifications, same virtual timestamps
    assert (slow.signals, slow.outcomes) == (fast.signals, fast.outcomes)


def test_replay_day_is_the_wib_calendar_day() -> None:
    start, end = day_bounds(DAY)
    assert (start.astimezone(UTC), end.astimezone(UTC)) == (
        datetime(2026, 10, 12, 17, 0, tzinfo=UTC),
        datetime(2026, 10, 13, 17, 0, tzinfo=UTC),
    )
    assert day_bounds(DAY, *WINDOW)[1].astimezone(UTC) == datetime(2026, 10, 13, 12, 45, tzinfo=UTC)


async def test_bridge_round_trip(clock: ManualClock) -> None:
    clock.set(datetime(2026, 10, 13, 12, 30, tzinfo=UTC))
    fake = FakeMt5(clock)
    adapter = Mt5Adapter(symbol="XAUUSD", clock=clock, module=fake, offset_hours=3)
    bus = MemoryBus()
    receiver = BridgeTickAdapter(bus)
    await bus.read("market.ticks.raw", "market", "m", block_ms=0)  # the core subscribes first
    publisher = BridgePublisher(adapter, bus, history={"M1": 1}, fx_symbols={"EURUSD": "EURUSD.m"})
    await publisher.publish_symbol_info()
    assert await publisher.publish_history() == 1
    await publisher.publish_fx()
    adapter.read_tick()  # first read only learns the server clock
    fake.quote_epoch += 1.0
    tick = adapter.read_tick()
    assert tick is not None
    await publisher._emit(tick)  # pyright: ignore[reportPrivateUsage]

    received: list[Tick] = []
    stop = asyncio.Event()

    async def emit(t: Tick) -> None:
        received.append(t)
        stop.set()

    await asyncio.wait_for(receiver.run(emit, stop), timeout=5)
    assert [t.bid for t in received] == [2650.1]
    assert await receiver.contract_size() == 100.0
    assert receiver.quotes({"EURUSD": "EURUSD.m"}) == {"EURUSD": 2650.25}
    (candle,) = await receiver.history("M1", 10)
    assert candle.open_time == datetime(2026, 10, 13, 12, 28, tzinfo=UTC)
    restarted = BridgeTickAdapter(bus, consumer="market-2")  # a core restart: history again
    assert [c.open_time for c in await restarted.history("M1", 10)] == [candle.open_time]
    clock.advance(600)
    await publisher.publish_history()  # the bridge's 10-minute refresh: the newest one wins
    (fresh,) = await restarted.history("M1", 10)
    assert fresh.open_time == candle.open_time + timedelta(minutes=10)

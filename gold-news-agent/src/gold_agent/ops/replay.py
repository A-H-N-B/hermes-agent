"""Replay mode (section 18): play one recorded day of archived news and stored ticks through the
real pipeline on an accelerated clock, into a separate scratch database.

Collector -> analyzer -> signal engine -> paper tracker -> notifier run unchanged; only the inputs
differ: a feeder publishes archived items at their original first_seen_at and a price adapter
emits stored ticks at their original timestamps. The LLM is off unless asked for (cost), and the
notifier has no Telegram sender, so nothing leaves the machine.

The clock stands still while the pipeline works (see `ReplayClock`): start-up, every emitted
input, every bus message until all consumers acknowledged it and every periodic cycle take no
replay time. A replay is therefore deterministic and the same at any `--speed` (the speed only
caps how fast it may go), and processing lag never trips the age guards (stale flash, old
story). It shows what the system decides, not how fast: latency is measured live. Background
deep analyses (`--llm`) are the exception: they run in real time, so keep `--speed` low when
judging their timing.
"""

from __future__ import annotations

import asyncio
import time as wall
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import structlog
from pydantic import BaseModel
from sqlalchemy import event

from gold_agent.config import Settings
from gold_agent.core.app import AppContext, build_context
from gold_agent.core.bus import MemoryBus, Message, StartAt
from gold_agent.core.clock import ReplayClock
from gold_agent.core.service import Service, supervise
from gold_agent.market.adapters.base import TickSink
from gold_agent.models import CalendarEvent, Candle, NewsItem, Tick
from gold_agent.models.common import Timeframe
from gold_agent.timeutils.sessions import WIB

log = structlog.get_logger(__name__)

WARMUP = timedelta(days=10)  # bars before the day so indicators are warm at its start
STARTUP_SECONDS = 120.0  # real seconds the services get to come up (history load included)
SETTLED_TURNS = 3  # consecutive quiet event-loop turns that mean start-up is over


@dataclass
class ReplayPlan:
    start: datetime
    end: datetime
    items: list[NewsItem]
    ticks: list[Tick]
    events: list[CalendarEvent]
    history: dict[Timeframe, list[Candle]] = field(
        default_factory=lambda: dict[Timeframe, list[Candle]]()
    )

    @property
    def source(self) -> str | None:
        return self.ticks[0].source if self.ticks else None


@dataclass
class ReplaySummary:
    start: datetime
    end: datetime
    items: int
    ticks: int
    speed: float  # requested
    wall_seconds: float
    clusters: int = 0
    analyses: int = 0
    relevant: int = 0
    releases: int = 0
    signals: list[str] = field(default_factory=lambda: list[str]())
    outcomes: list[str] = field(default_factory=lambda: list[str]())
    notifications: dict[str, int] = field(default_factory=lambda: dict[str, int]())

    def render(self) -> str:
        start, end = self.start.astimezone(WIB), self.end.astimezone(WIB)
        effective = (end - start).total_seconds() / self.wall_seconds if self.wall_seconds else 0
        lines = [
            f"Replay {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} WIB",
            f"- kecepatan efektif {effective:.0f}× (diminta {self.speed:g}×)",
            f"- berita diputar: {self.items} → cluster {self.clusters}, analisis {self.analyses} "
            f"(relevan {self.relevant}), rilis data {self.releases}",
            f"- tick diputar: {self.ticks}",
            f"- sinyal: {len(self.signals)}"
            + (f" ({', '.join(self.signals)})" if self.signals else ""),
            f"- hasil paper: {', '.join(self.outcomes) or '-'}",
            "- notifikasi (tidak dikirim): "
            + (", ".join(f"{k} {v}" for k, v in sorted(self.notifications.items())) or "-"),
        ]
        return "\n".join(lines)


def day_bounds(
    day: date, start_time: time | None = None, end_time: time | None = None
) -> tuple[datetime, datetime]:
    """The WIB calendar day, or a window inside it (e.g. 19:00-22:00 around US data)."""
    start = datetime.combine(day, start_time or time(0, 0), tzinfo=WIB)
    if end_time is None:
        return start, datetime.combine(day, time(0, 0), tzinfo=WIB) + timedelta(days=1)
    return start, datetime.combine(day, end_time, tzinfo=WIB)


async def load_plan(
    source: AppContext, day: date, *, start_time: time | None = None, end_time: time | None = None
) -> ReplayPlan:
    start, end = day_bounds(day, start_time, end_time)
    ticks = await source.market.ticks_between(None, start, end)
    counts = Counter(t.source for t in ticks)
    main = counts.most_common(1)[0][0] if counts else None
    ticks = [t for t in ticks if t.source == main]
    history: dict[Timeframe, list[Candle]] = {}
    if main is not None:
        for tf in source.settings.market.history_bars:
            history[tf] = await source.market.candles(tf, start - WARMUP, start, source=main)
    return ReplayPlan(
        start=start,
        end=end,
        items=sorted(
            await source.news.items_between(start, end), key=lambda item: item.first_seen_at
        ),
        ticks=ticks,
        events=await source.calendar.between(start - timedelta(days=1), end + timedelta(days=1)),
        history=history,
    )


class PacedBus(MemoryBus):
    """Holds the replay clock while any consumer group has unread or unacknowledged messages,
    so replay time only moves on once every consumer has caught up."""

    def __init__(self, clock: ReplayClock) -> None:
        super().__init__()
        self._clock = clock
        self._groups: set[tuple[str, str]] = set()
        self._unacked = 0
        self._holding = False
        self.activity = 0  # messages published, delivered or acknowledged

    @property
    def idle(self) -> bool:
        return not self._holding

    async def _pace(self) -> None:
        busy = self._unacked > 0
        for stream, group in self._groups:
            if busy:
                break
            busy = bool(await self.lag(stream, group))
        if busy != self._holding:
            self._holding = busy
            if busy:
                self._clock.hold()
            else:
                self._clock.release()

    async def publish(self, stream: str, payload: BaseModel | Mapping[str, Any]) -> str:
        message_id = await super().publish(stream, payload)
        self.activity += 1
        await self._pace()
        return message_id

    async def read(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 50,
        block_ms: int = 1000,
        start: StartAt = "earliest",
    ) -> list[Message]:
        self._groups.add((stream, group))
        messages = await super().read(
            stream, group, consumer, count=count, block_ms=block_ms, start=start
        )
        self._unacked += len(messages)
        self.activity += len(messages)
        await self._pace()
        return messages

    async def ack(self, stream: str, group: str, ids: Sequence[str]) -> None:
        self._unacked -= len(ids)
        self.activity += len(ids)
        await self._pace()


class DbActivity:
    """Connections in use and checked out so far, from SQLAlchemy's pool events: start-up
    work is DB work, so it is over once no connection is in use and none was taken lately."""

    def __init__(self, ctx: AppContext) -> None:
        self.in_use = 0
        self.checkouts = 0
        engine = ctx.db.engine.sync_engine
        event.listen(engine, "checkout", self._checkout)
        event.listen(engine, "checkin", self._checkin)

    def _checkout(self, *_args: Any) -> None:
        self.in_use += 1
        self.checkouts += 1

    def _checkin(self, *_args: Any) -> None:
        self.in_use -= 1


async def settled(bus: PacedBus, db: DbActivity) -> None:
    """Return once the services are parked: no DB connection in use, nothing queued on the
    bus, and no new DB or bus activity for a few event-loop turns (a task that was still
    runnable would have touched one of them)."""
    quiet, last = 0, (-1, -1)
    while quiet < SETTLED_TURNS:
        await asyncio.sleep(0.001)
        mark = (db.checkouts, bus.activity)
        quiet = quiet + 1 if mark == last and db.in_use == 0 and bus.idle else 0
        last = mark


class ReplayPriceAdapter:
    """Emits stored ticks at their original timestamps on the replay clock. A restart by the
    market service's supervisor resumes after the last emitted tick."""

    proxy = False

    def __init__(self, plan: ReplayPlan, clock: ReplayClock) -> None:
        self.name = plan.source or "replay"
        self.started = asyncio.Event()  # the market service has loaded history and consumes
        self._plan = plan
        self._clock = clock
        self._next = 0

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        self.started.set()
        ticks = self._plan.ticks
        while self._next < len(ticks):
            tick = ticks[self._next]
            async with self._clock.at(tick.ts):
                if stop.is_set():
                    return
                await emit(tick.model_copy(update={"received_at": self._clock.now()}))
            self._next += 1
        await stop.wait()

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        return self._plan.history.get(tf, [])[-count:]

    async def contract_size(self) -> float | None:
        return None

    async def volume_step(self) -> float | None:
        return None


class ReplayNewsFeeder:
    """Stands in for the collectors: archived items go through the real dedup/cluster step."""

    name = "replay-news"

    def __init__(self, ctx: AppContext, clock: ReplayClock, items: Sequence[NewsItem]) -> None:
        from gold_agent.services.collector import CollectorService

        self._clock = clock
        self._items = items
        self._collector = CollectorService(ctx)
        self._next = 0

    async def run(self, stop: asyncio.Event) -> None:
        clock = self._clock
        while self._next < len(self._items):
            item = self._items[self._next]
            async with clock.at(item.first_seen_at):
                if stop.is_set():
                    return
                await self._collector.ingest(item)
            self._next += 1
        await stop.wait()


def replay_settings(settings: Settings, *, use_llm: bool) -> Settings:
    """Recorded inputs only: no live USD feed mixed into a past day, LLM only on request."""
    llm = settings.llm.model_copy(update={"enabled": use_llm})
    outlook = settings.outlook.model_copy(update={"use_llm": use_llm})
    market = settings.market.model_copy(update={"usd_source": "none"})
    return settings.model_copy(update={"llm": llm, "outlook": outlook, "market": market})


async def run_replay(
    source: AppContext,
    day: date,
    *,
    database_url: str,
    speed: float = 1000.0,
    use_llm: bool = False,
    start_time: time | None = None,
    end_time: time | None = None,
) -> ReplaySummary:
    plan = await load_plan(source, day, start_time=start_time, end_time=end_time)
    clock = ReplayClock(plan.start, speed, held=True)  # set-up and start-up take no replay time
    bus = PacedBus(clock)
    ctx = build_context(
        settings=replay_settings(source.settings, use_llm=use_llm),
        sources=source.sources,
        secrets=source.secrets,
        database_url=database_url,
        bus=bus,
        clock=clock,
    )
    # No keys, no live calls (FRED, Telegram, ...); only the LLM key, and only when asked for.
    key = source.env.get("ANTHROPIC_API_KEY")
    ctx.env = {"ANTHROPIC_API_KEY": key} if use_llm and key else {}
    try:
        await ctx.db.create_all()
        await ctx.calendar.upsert_events(plan.events, plan.start)
        for candles in plan.history.values():  # as in production: history is already stored
            await ctx.market.upsert_candles(candles)
        feeder = ReplayNewsFeeder(ctx, clock, plan.items)
        prices = ReplayPriceAdapter(plan, clock)
        log.info(
            "replay.start", day=str(day), items=len(plan.items), ticks=len(plan.ticks), speed=speed
        )
        began = wall.monotonic()
        services = _services(ctx, feeder, prices)
        await _play(clock, bus, DbActivity(ctx), plan.end, services, prices.started)
        return await summarize_replay(ctx, plan, speed=speed, wall_seconds=wall.monotonic() - began)
    finally:
        await ctx.close()


async def _play(
    clock: ReplayClock,
    bus: PacedBus,
    db: DbActivity,
    end: datetime,
    services: list[Service],
    started: asyncio.Event,
) -> None:
    """Run the services until the replay clock reaches `end`. The clock starts once every
    service has finished starting up, so start-up timing never shows in the results."""
    stop = asyncio.Event()
    runner = asyncio.create_task(supervise(services, stop))
    try:
        async with asyncio.timeout(STARTUP_SECONDS):
            await started.wait()  # the market service loaded its history and reads the ticks
            await settled(bus, db)
        clock.release()
        await clock.sleep((end - clock.now()).total_seconds())
    finally:
        stop.set()
        await runner


def _services(
    ctx: AppContext, feeder: ReplayNewsFeeder, prices: ReplayPriceAdapter
) -> list[Service]:
    from gold_agent.services.analyzer import AnalyzerService
    from gold_agent.services.market import MarketService
    from gold_agent.services.notifier import NotifierService
    from gold_agent.services.paper import PaperService
    from gold_agent.services.signal import SignalService

    return [
        feeder,
        MarketService(ctx, adapter=prices),
        AnalyzerService(ctx),
        SignalService(ctx),
        PaperService(ctx),
        NotifierService(ctx, poll=False),
    ]


async def summarize_replay(
    ctx: AppContext, plan: ReplayPlan, *, speed: float, wall_seconds: float
) -> ReplaySummary:
    start, end = plan.start, plan.end + timedelta(days=1)
    analyses = await ctx.news.analyses_between(start, end)
    signals = await ctx.signals.signals_between(start, end)
    outcomes = await ctx.signals.outcomes_between(start, end)
    notes = await ctx.notify.recent(limit=1000)
    return ReplaySummary(
        start=plan.start,
        end=plan.end,
        items=len(plan.items),
        ticks=len(plan.ticks),
        speed=speed,
        wall_seconds=wall_seconds,
        clusters=len(await ctx.news.recent_clusters(start)),
        analyses=len(analyses),
        relevant=sum(a.triage.is_gold_relevant for a in analyses),
        releases=sum(a.origin == "fast_path" for a in analyses),
        signals=[f"{s.action} {s.created_at_wib}" for s in signals],
        outcomes=[
            f"{o.status} {o.r_multiple:+.2f}R" if o.r_multiple is not None else o.status
            for o in outcomes
        ],
        notifications=dict(Counter(n.kind for n in notes)),
    )

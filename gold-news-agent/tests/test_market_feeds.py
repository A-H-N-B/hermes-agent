from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_ALERTS, STREAM_CONTEXT
from gold_agent.core.clock import ManualClock
from gold_agent.market.adapters.base import TickSink
from gold_agent.market.adapters.binance import BinanceStreamParser, parse_klines
from gold_agent.market.adapters.failover import FailoverAdapter
from gold_agent.market.adapters.mt5 import Mt5Adapter, detect_offset_hours, server_to_utc
from gold_agent.market.adapters.twelvedata import (
    TwelveDataError,
    check_response,
    parse_time_series,
)
from gold_agent.models import Candle, MarketContext, Tick
from gold_agent.models.common import Timeframe
from gold_agent.services.market import MarketService


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# --------------------------------------------------------------------------- Binance


def test_binance_combined_stream_yields_ticks_with_bid_ask_and_trades() -> None:
    parser = BinanceStreamParser("binance_paxg", proxy=True)
    now = utc("2026-10-13T12:30")
    book = json.dumps(
        {
            "stream": "paxgusdt@bookTicker",
            "data": {"u": 1, "s": "PAXGUSDT", "b": "2650.10", "B": "1", "a": "2650.60", "A": "2"},
        }
    )
    trade = json.dumps(
        {
            "stream": "paxgusdt@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": 1792024200123,
                "s": "PAXGUSDT",
                "p": "2650.40",
                "q": "0.5",
                "T": 1792024200100,
            },
        }
    )
    first = parser.parse(book, now)
    assert first is not None and first.bid == 2650.10 and first.ask == 2650.60 and first.proxy
    second = parser.parse(trade, now)
    assert second is not None and second.last == 2650.40 and second.volume == 0.5
    assert second.ts == datetime.fromtimestamp(1792024200.1, UTC)
    assert parser.parse(json.dumps({"result": None, "id": 1}), now) is None


def test_binance_klines_mark_the_running_bar_incomplete() -> None:
    rows = [
        [1792022400000, "1", "2", "0.5", "1.5", "10", 1792022459999, "0", 7],
        [1792022460000, "1.5", "2.5", "1", "2", "5", 1792022519999, "0", 3],
    ]
    candles = parse_klines(rows, source="b", tf="M1", now=datetime.fromtimestamp(1792022500, UTC))
    assert [c.complete for c in candles] == [True, False]
    assert candles[0].ticks == 7 and candles[0].close == 1.5


# --------------------------------------------------------------------------- Twelve Data


def test_twelvedata_time_series_is_oldest_first_and_errors_raise() -> None:
    payload = {
        "values": [
            {
                "datetime": "2026-10-13 12:31:00",
                "open": "2",
                "high": "3",
                "low": "1",
                "close": "2.5",
            },
            {
                "datetime": "2026-10-13 12:30:00",
                "open": "1",
                "high": "2",
                "low": "0.5",
                "close": "2",
            },
        ],
        "status": "ok",
    }
    candles = parse_time_series(payload, source="td", tf="M1", now=utc("2026-10-13T12:31:30"))
    assert [c.open_time.minute for c in candles] == [30, 31]
    assert [c.complete for c in candles] == [True, False]
    with pytest.raises(TwelveDataError):
        check_response({"status": "error", "message": "API key invalid"})


# --------------------------------------------------------------------------- MT5


def test_mt5_offset_detection_and_conversion() -> None:
    now = utc("2026-10-13T12:30")
    server_epoch = now.timestamp() + 3 * 3600 + 2  # broker server at UTC+3, tick 2s old
    assert detect_offset_hours(server_epoch, now) == 3
    assert server_to_utc(server_epoch, 3) == now + timedelta(seconds=2)
    assert detect_offset_hours(now.timestamp() + 20 * 3600, now) is None


class FakeMt5:
    TIMEFRAME_M1 = 1

    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.server_offset = 3 * 3600
        self.quote_epoch = clock.now().timestamp() + self.server_offset

    def initialize(self, **_: Any) -> bool:
        return True

    def symbol_select(self, *_: Any) -> bool:
        return True

    def last_error(self) -> tuple[int, str]:
        return (0, "")

    def symbol_info_tick(self, symbol: str) -> Any:
        return SimpleNamespace(
            bid=2650.1,
            ask=2650.4,
            last=0.0,
            time=int(self.quote_epoch),
            time_msc=int(self.quote_epoch * 1000),
            volume_real=0.0,
        )

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(trade_contract_size=100.0, volume_step=0.01)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any:
        base = self.clock.now().timestamp() + self.server_offset - 120
        return [
            {
                "time": base - base % 60,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "tick_volume": 9,
            }
        ]


async def test_mt5_adapter_waits_for_a_fresh_tick_then_converts_server_time() -> None:
    clock = ManualClock(utc("2026-10-13T12:30"))
    fake = FakeMt5(clock)
    adapter = Mt5Adapter(symbol="XAUUSD", clock=clock, module=fake)
    assert adapter.read_tick() is None  # first read: freshness unknown, offset unknown
    fake.quote_epoch += 1.5  # a new tick arrives
    tick = adapter.read_tick()
    assert tick is not None and tick.ts == utc("2026-10-13T12:30") + timedelta(seconds=1.5)
    assert await adapter.contract_size() == 100.0
    (candle,) = await adapter.history("M1", 1)
    assert candle.open_time == utc("2026-10-13T12:28")


# --------------------------------------------------------------------------- failover


class FlakyAdapter:
    def __init__(self, name: str, fail: bool) -> None:
        self.name = name
        self.proxy = False
        self.fail = fail
        self.runs = 0

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        self.runs += 1
        if self.fail:
            raise ConnectionError("down")
        now = datetime.now(UTC)
        await emit(Tick(ts=now, bid=1, ask=2, source=self.name, received_at=now))
        await stop.wait()

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        if self.fail:
            raise ConnectionError("down")
        return []

    async def contract_size(self) -> float | None:
        return None

    async def volume_step(self) -> float | None:
        return None


async def test_failover_moves_to_the_next_feed_after_repeated_failures() -> None:
    from gold_agent.core.clock import SystemClock

    primary, backup = FlakyAdapter("primary", fail=True), FlakyAdapter("backup", fail=False)
    adapter = FailoverAdapter([primary, backup], SystemClock(), max_failures=2)
    received: list[Tick] = []
    stop = asyncio.Event()

    async def emit(tick: Tick) -> None:
        received.append(tick)
        stop.set()

    import gold_agent.market.adapters.failover as failover_module

    async def no_sleep(stop_event: asyncio.Event, seconds: float) -> bool:
        await asyncio.sleep(0)
        return stop_event.is_set()

    original = failover_module.sleep_or_stop
    failover_module.sleep_or_stop = no_sleep  # type: ignore[assignment]
    try:
        await asyncio.wait_for(adapter.run(emit, stop), timeout=5)
    finally:
        failover_module.sleep_or_stop = original
    assert primary.runs == 2 and received and received[0].source == "backup"
    assert adapter.name == "backup"


class QuietAdapter(FlakyAdapter):
    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        self.runs += 1
        await stop.wait()


async def test_silent_feed_fails_over_only_while_the_market_is_open() -> None:
    from gold_agent.core.clock import SystemClock

    for market_open, expected in ((False, "quiet"), (True, "backup")):
        quiet, backup = QuietAdapter("quiet", fail=False), FlakyAdapter("backup", fail=False)
        adapter = FailoverAdapter(
            [quiet, backup],
            SystemClock(),
            max_failures=1,
            silence_seconds=0.05,
            check_seconds=0.02,
            market_open=lambda _now, is_open=market_open: is_open,
        )
        stop = asyncio.Event()

        async def emit(tick: Tick) -> None:
            stop.set()

        task = asyncio.create_task(adapter.run(emit, stop))
        await asyncio.sleep(0.4 if market_open else 0.3)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        assert adapter.name == expected


# --------------------------------------------------------------------------- market service


class ScriptedAdapter:
    name = "scripted"
    proxy = True

    def __init__(self, ticks: list[Tick], history: dict[str, list[Candle]]) -> None:
        self._ticks = ticks
        self._history = history

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        for tick in self._ticks:
            await emit(tick)
        await stop.wait()

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        return self._history.get(tf, [])[-count:]

    async def contract_size(self) -> float | None:
        return None

    async def volume_step(self) -> float | None:
        return None


def h1_history(end: datetime, hours: int) -> list[Candle]:
    candles: list[Candle] = []
    price = 2600.0
    for i in range(hours, 0, -1):
        open_time = end - timedelta(hours=i)
        price += 1.0 if i % 3 else -0.5
        candles.append(
            Candle(
                source="scripted",
                tf="H1",
                open_time=open_time,
                close_time=open_time + timedelta(hours=1),
                open=price - 0.5,
                high=price + 2,
                low=price - 2,
                close=price,
                volume=10,
                complete=True,
            )
        )
    return candles


async def test_market_service_builds_context_and_flags_stale_prices(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> None:
    context = ctx_factory()
    await context.db.create_all()
    clock.set(utc("2026-10-13T12:30"))
    ticks = [
        Tick(
            ts=clock.now(),
            bid=2700.0,
            ask=2700.4,
            volume=0.2,
            source="scripted",
            proxy=True,
            received_at=clock.now(),
        )
    ]
    adapter = ScriptedAdapter(ticks, {"H1": h1_history(utc("2026-10-13T12:00"), 400)})
    service = MarketService(context, adapter=adapter)
    try:
        await service.load_history(adapter)
        for tick in ticks:
            await service.on_tick(tick)
        snapshot = await service.publish_context()
        assert snapshot.mid == pytest.approx(2700.2) and not snapshot.stale
        assert snapshot.market_open and snapshot.session == "overlap"
        assert snapshot.timeframes["H1"].ema50 is not None
        assert snapshot.timeframes["D1"].bars > 10
        assert snapshot.prev_day_high is not None
        assert any("proxy" in note for note in snapshot.notes)
        clock.advance(120)  # no ticks for two minutes during market hours
        stale = await service.publish_context()
        assert stale.stale
        alerts = await context.bus.read(STREAM_ALERTS, "t", "t", block_ms=0)
        assert [a.data["kind"] for a in alerts] == ["stale_price"]
        published = await context.bus.read(STREAM_CONTEXT, "t", "t", block_ms=0)
        assert MarketContext.model_validate(published[-1].data).stale
        assert await context.market.latest_context() is not None
    finally:
        await context.close()


async def test_weekend_proxy_ticks_feed_the_gap_estimate_not_the_candles(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> None:
    context = ctx_factory()
    await context.db.create_all()
    service = MarketService(context, adapter=ScriptedAdapter([], {}))
    try:
        friday = utc("2026-10-16T20:00")  # Friday 16:00 New York, market open
        saturday = utc("2026-10-17T10:00")
        await service.on_tick(
            Tick(ts=friday, bid=2700, ask=2700, source="p", proxy=True, received_at=friday)
        )
        await service.on_tick(
            Tick(ts=saturday, bid=2712, ask=2712, source="p", proxy=True, received_at=saturday)
        )
        assert service.weekend.drift == pytest.approx(12)
        assert all(c.open_time < saturday for c in service.builder.candles("M1"))
        clock.set(saturday)
        snapshot = await service.publish_context()
        assert not snapshot.market_open and snapshot.weekend_drift == pytest.approx(12)
    finally:
        await context.close()


async def test_market_without_any_verified_feed_reports_missing_data(ctx: AppContext) -> None:
    service = MarketService(ctx, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    snapshot = service.context(ctx.clock.now())
    assert snapshot.stale and "data harga tidak tersedia" in snapshot.notes

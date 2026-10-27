from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from gold_agent.config import Settings, SourceStatus, load_sources
from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_ALERTS, STREAM_CONTEXT
from gold_agent.core.clock import ManualClock
from gold_agent.market.adapters.base import TickSink
from gold_agent.market.adapters.binance import BinanceStreamParser, parse_klines
from gold_agent.market.adapters.factory import build_price_adapter, feed_order
from gold_agent.market.adapters.failover import FailoverAdapter
from gold_agent.market.adapters.mt5 import (
    Mt5Adapter,
    detect_offset_hours,
    pick_gold_symbol,
    server_to_utc,
)
from gold_agent.market.adapters.twelvedata import (
    TwelveDataError,
    check_response,
    parse_time_series,
)
from gold_agent.market.econophysics import PE_STRUCTURED
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


async def test_spot_gold_comes_from_the_xauusdt_perpetual_book_and_futures_klines(
    ctx_factory: Callable[..., AppContext], settings: Settings
) -> None:
    """The XAUUSDT perpetual (spot gold) runs on Binance's USD-M futures hosts: its bars come
    from /fapi/v1/klines (1500 a request), its prices from the /public bookTicker stream."""
    registry = load_sources(local=False)
    verified = [
        registry.get(sid).model_copy(update={"status": SourceStatus.VERIFIED})
        for sid in ("binance_xau_ws", "binance_xau_rest")
    ]
    assert verified[0].url and "/public/" in verified[0].url  # bookTicker is a /public stream
    feed = settings.price_feed.model_copy(update={"primary": "binance_xau", "fallbacks": ()})
    ctx = ctx_factory(verified, settings_override=settings.model_copy(update={"price_feed": feed}))
    requests: list[httpx.Request] = []
    start = 1792022400000

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        limit = int(request.url.params["limit"])
        end = int(request.url.params.get("endTime", start + 2000 * 60_000))
        rows = [
            [t, "3999", "4000", "3998", "3999.5", "1", t + 59_999, "0", 4]
            for t in range(end - limit * 60_000 + 1, end, 60_000)
        ][-limit:]
        return httpx.Response(200, json=rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        adapter = build_price_adapter(ctx, client)
        assert adapter is not None and adapter.name == "binance_xau" and adapter.proxy
        bars = await adapter.history("M1", 2000)
    await ctx.close()
    assert len(bars) == 2000 and {b.source for b in bars} == {"binance_xau"}
    assert [r.url.path for r in requests] == ["/fapi/v1/klines"] * 2
    assert requests[0].url.host == "fapi.binance.com"
    assert (
        requests[0].url.params["symbol"] == "XAUUSDT" and requests[0].url.params["limit"] == "1500"
    )
    parser = BinanceStreamParser("binance_xau", proxy=True)
    book = {
        "stream": "xauusdt@bookTicker",
        "data": {
            "e": "bookTicker",
            "u": 9,
            "E": 1792024200123,
            "T": 1792024200120,
            "s": "XAUUSDT",
            "b": "3999.12",
            "B": "3",
            "a": "3999.25",
            "A": "2",
        },
    }
    tick = parser.parse(json.dumps(book), utc("2026-10-13T12:30"))
    assert tick is not None and (tick.bid, tick.ask, tick.source) == (
        3999.12,
        3999.25,
        "binance_xau",
    )


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

    def __init__(self, clock: ManualClock, account: int | None = 777) -> None:
        self.clock = clock
        self.server_offset = 3 * 3600
        self.quote_epoch = clock.now().timestamp() + self.server_offset
        self.account = account
        self.logins: list[tuple[int, str | None]] = []
        self.initializations = 0

    def initialize(self, **_: Any) -> bool:
        self.initializations += 1
        return True

    def account_info(self) -> Any:
        return SimpleNamespace(login=self.account) if self.account else None

    def login(self, login: int, *, password: str | None, server: str | None) -> bool:
        self.logins.append((login, server))
        self.account = login
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


def symbol(name: str, *, visible: bool = False, trade_mode: int = 4, profit: str = "USD") -> Any:
    return SimpleNamespace(
        name=name, visible=visible, trade_mode=trade_mode, currency_profit=profit
    )


def test_the_brokers_gold_symbol_is_found_like_a_trader_would() -> None:
    listed = [
        symbol("XAGUSD", visible=True),  # silver
        symbol("XAUEUR", visible=True),  # gold in euro
        symbol("GOLDEUR", visible=True),
        symbol("XAUUSD.r", visible=True, profit="EUR"),  # a USD name quoted in EUR
        symbol("XAUUSD"),
        symbol("XAUUSDm", visible=True),  # in Market Watch: the one the user trades
        symbol("GOLD", visible=True),
    ]
    assert pick_gold_symbol(listed) == "XAUUSDm"
    hidden = [symbol("GOLD.i"), symbol("XAUUSD+", trade_mode=0), symbol("XAUUSD.pro")]
    assert pick_gold_symbol(hidden) == "XAUUSD.pro"  # XAUUSD before GOLD, tradable first
    assert pick_gold_symbol([symbol("GOLD.i"), symbol("EURUSD")]) == "GOLD.i"
    assert pick_gold_symbol([symbol("XAGUSD"), symbol("EURUSD")]) is None


async def test_mt5_adapter_without_a_symbol_uses_the_detected_one() -> None:
    clock = ManualClock(utc("2026-10-13T12:30"))
    fake = FakeMt5(clock)
    fake.symbols_get = lambda: [symbol("XAGUSD"), symbol("XAUUSDm", visible=True)]  # type: ignore[attr-defined]
    selected: list[str] = []
    fake.symbol_select = lambda name, _on: selected.append(name) or True  # type: ignore[method-assign]
    adapter = Mt5Adapter(symbol=None, clock=clock, module=fake)
    adapter.read_tick()
    assert selected == ["XAUUSDm"]


def test_auto_feed_prefers_the_broker_then_spot_gold_then_the_gold_token() -> None:
    assert feed_order("auto", ("twelvedata",), mt5=True) == [
        "mt5",
        "binance_xau",
        "binance_paxg",
        "twelvedata",
    ]
    assert feed_order("auto", ("twelvedata",), mt5=False) == [
        "binance_xau",
        "binance_paxg",
        "twelvedata",
    ]
    assert feed_order("mt5", ("binance_paxg", "mt5"), mt5=False) == ["mt5", "binance_paxg"]


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


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    import gold_agent.market.adapters.failover as failover_module

    async def no_sleep(stop_event: asyncio.Event, seconds: float) -> bool:
        await asyncio.sleep(0)
        return stop_event.is_set()

    monkeypatch.setattr(failover_module, "sleep_or_stop", no_sleep)


class DroppingAdapter(FlakyAdapter):
    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        self.runs += 1
        now = datetime.now(UTC)
        await emit(Tick(ts=now, bid=1, ask=2, source=self.name, received_at=now))
        # ... and the server closes the stream (Binance does after 24 hours)


@pytest.mark.usefixtures("no_backoff")
async def test_a_stream_that_drops_after_delivering_is_reconnected_not_failed_over() -> None:
    from gold_agent.core.clock import SystemClock

    primary = DroppingAdapter("binance_xau", fail=False)
    backup = FlakyAdapter("binance_paxg", fail=False)
    adapter = FailoverAdapter([primary, backup], SystemClock(), max_failures=2)
    stop = asyncio.Event()

    async def emit(tick: Tick) -> None:
        if primary.runs == 5:
            stop.set()

    await asyncio.wait_for(adapter.run(emit, stop), timeout=5)
    assert backup.runs == 0 and adapter.name == "binance_xau"


class RecoveringAdapter(FlakyAdapter):
    """Down for its first `down_runs` runs, then delivering."""

    def __init__(self, name: str, down_runs: int) -> None:
        super().__init__(name, fail=True)
        self.down_runs = down_runs

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        self.fail = self.runs < self.down_runs
        await super().run(emit, stop)


@pytest.mark.usefixtures("no_backoff")
async def test_the_feed_in_use_never_stops_while_the_primary_is_tried_again() -> None:
    from gold_agent.core.clock import SystemClock

    primary = RecoveringAdapter("binance_xau", down_runs=2)
    backup = FlakyAdapter("binance_paxg", fail=False)
    adapter = FailoverAdapter(
        [primary, backup],
        SystemClock(),
        max_failures=1,
        retry_primary_seconds=0.05,
        probe_seconds=0.5,
    )
    sources: list[str] = []
    stop = asyncio.Event()

    async def emit(tick: Tick) -> None:
        sources.append(tick.source)
        if tick.source == "binance_xau":
            stop.set()

    await asyncio.wait_for(adapter.run(emit, stop), timeout=5)
    assert sources == ["binance_paxg", "binance_xau"]  # a try's ticks are never emitted
    assert backup.runs == 1  # the try that failed did not interrupt it
    assert primary.runs == 4  # down, tried in vain, tried and up, then used


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

        async def emit(tick: Tick, done: asyncio.Event = stop) -> None:
            done.set()

        task = asyncio.create_task(adapter.run(emit, stop))
        await asyncio.sleep(0.4 if market_open else 0.3)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        assert adapter.name == expected


class StreamingAdapter(FlakyAdapter):
    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        self.runs += 1
        while not stop.is_set():
            now = datetime.now(UTC)
            await emit(Tick(ts=now, bid=1, ask=2, source=self.name, received_at=now))
            await asyncio.sleep(0.01)


@pytest.mark.usefixtures("no_backoff")
async def test_a_primary_that_hangs_is_given_up_on_while_the_feed_in_use_runs_on() -> None:
    from gold_agent.core.clock import SystemClock

    quiet = QuietAdapter("mt5", fail=False)  # connects, never sends a price
    backup = StreamingAdapter("binance_xau", fail=False)
    adapter = FailoverAdapter(
        [quiet, backup],
        SystemClock(),
        max_failures=1,
        silence_seconds=0.05,
        check_seconds=0.01,
        retry_primary_seconds=0.05,
        probe_seconds=0.05,
    )
    stop = asyncio.Event()

    async def emit(tick: Tick) -> None:
        pass

    task = asyncio.create_task(adapter.run(emit, stop))
    await asyncio.sleep(0.6)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert adapter.name == "binance_xau" and backup.runs == 1
    assert quiet.runs >= 3  # silent at first, then tried several times beside the backup


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
        h1 = snapshot.timeframes["H1"]  # the econophysics of the chart rides along
        assert h1.entropy is not None and h1.entropy < PE_STRUCTURED  # one repeating pattern
        assert h1.tail_alpha is not None and h1.mf_width is not None and h1.ou_t is not None
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


def m1_bars(end: datetime, count: int, source: str, price: float) -> list[Candle]:
    return [
        Candle(
            source=source,
            tf="M1",
            open_time=end - timedelta(minutes=count - i),
            close_time=end - timedelta(minutes=count - i - 1),
            open=price,
            high=price + 0.5,
            low=price - 0.5,
            close=price,
            complete=True,
        )
        for i in range(count)
    ]


async def test_a_feed_switch_reloads_history_instead_of_mixing_two_instruments(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> None:
    context = ctx_factory()
    await context.db.create_all()
    now = utc("2026-10-13T12:30")
    clock.set(now)
    broker = ScriptedAdapter([], {"M1": m1_bars(now, 30, "mt5", 2680.0)})
    service = MarketService(context, adapter=broker)
    try:
        service.builder.seed("M1", m1_bars(now, 30, "binance_paxg", 2695.0))  # PAXG premium
        service.builder.source = "binance_paxg"
        tick = Tick(ts=now, bid=2680.0, ask=2680.3, source="mt5", received_at=now)
        await service.on_tick(tick)
        bars = service.builder.candles("M1")
        assert {bar.source for bar in bars} == {"mt5"}
        assert max(bar.high for bar in bars) < 2690  # no PAXG bar left to fake a jump
    finally:
        await context.close()


def test_mt5_reads_the_logged_in_account_and_never_logs_in_itself() -> None:
    clock = ManualClock(utc("2026-10-13T12:30"))
    logged_out = FakeMt5(clock, account=None)
    adapter = Mt5Adapter(symbol="XAUUSD", clock=clock, module=logged_out)
    with pytest.raises(ConnectionError, match="belum login"):
        adapter.read_tick()
    logged_out.account = 777  # the user logs in in MT5
    adapter.read_tick()
    logged_out.quote_epoch += 1.5
    assert adapter.read_tick() is not None
    assert logged_out.logins == [] and logged_out.account == 777


def test_mt5_is_only_attached_to_never_started_by_the_agent() -> None:
    clock = ManualClock(utc("2026-10-13T12:30"))
    fake = FakeMt5(clock)
    terminal = SimpleNamespace(open=False)
    adapter = Mt5Adapter(symbol="XAUUSD", clock=clock, module=fake, running=lambda: terminal.open)
    with pytest.raises(ConnectionError, match="tidak terbuka"):
        adapter.read_tick()
    assert fake.initializations == 0  # initialize() would have started the terminal itself
    terminal.open = True  # the user opens MT5
    adapter.read_tick()
    assert fake.initializations == 1 and fake.logins == []


async def test_every_feed_down_goes_back_to_the_primary_and_says_why() -> None:
    import gold_agent.market.adapters.failover as failover_module
    from gold_agent.core.clock import SystemClock

    primary, backup = FlakyAdapter("mt5", fail=True), FlakyAdapter("binance", fail=True)
    adapter = FailoverAdapter([primary, backup], SystemClock(), max_failures=2)
    stop = asyncio.Event()

    async def emit(tick: Tick) -> None:
        stop.set()

    async def no_sleep(stop_event: asyncio.Event, seconds: float) -> bool:
        await asyncio.sleep(0)
        if backup.runs >= 2:
            primary.fail = False  # MT5 is back
        return stop_event.is_set()

    original = failover_module.sleep_or_stop
    failover_module.sleep_or_stop = no_sleep  # type: ignore[assignment]
    try:
        await asyncio.wait_for(adapter.run(emit, stop), timeout=5)
    finally:
        failover_module.sleep_or_stop = original
    assert adapter.name == "mt5" and primary.runs == 3  # not 15 minutes later
    assert adapter.problem is None  # ticks again

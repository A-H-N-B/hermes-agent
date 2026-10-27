"""Re-basing a proxy feed onto the broker's prices (market/rebase.py): after the user types the
bid their broker shows, every tick, every bar (in memory and stored) and every signal level of
the proxy feed reads like the broker, with no jump between old and new bars; never for a broker
feed or while a signal is active, and reversible."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_EVENTS
from gold_agent.core.clock import ManualClock
from gold_agent.market.rebase import KV_PRICE_OFFSET, Rebase, request_calibration
from gold_agent.models import Tick
from gold_agent.services.market import MarketService
from gold_agent.signals.engine import KV_ENGINE, EngineState
from tests.test_desk import candidate
from tests.test_market_feeds import ScriptedAdapter, h1_history

AT = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)


def tick(bid: float, at: datetime = AT, *, proxy: bool = True, source: str = "scripted") -> Tick:
    return Tick(
        ts=at, bid=bid, ask=bid + 0.4, source=source, proxy=proxy, received_at=at, volume=0.1
    )


def test_only_the_proxy_feed_it_belongs_to_moves() -> None:
    rebase = Rebase(source="scripted", offset=12.4, at=AT)
    assert rebase.tick(tick(2650.0)).bid == pytest.approx(2662.4)
    assert rebase.tick(tick(2650.0, source="other")).bid == 2650.0
    assert rebase.tick(tick(2650.0, proxy=False)).bid == 2650.0
    assert rebase.raw_bid_at(AT) == 2650.0  # what the feed itself said, for the next calibration


async def service_with_history(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> tuple[MarketService, AppContext]:
    context = ctx_factory()
    await context.db.create_all()
    clock.set(AT)
    adapter = ScriptedAdapter([], {"H1": h1_history(AT - timedelta(minutes=30), 300)})
    service = MarketService(context, adapter=adapter)
    await service.load_history(adapter)
    await service.on_tick(tick(2650.0))
    return service, context


async def stored_closes(context: AppContext) -> list[float]:
    bars = await context.market.candles("H1", AT - timedelta(hours=5), AT, source="scripted")
    return [bar.close for bar in bars]


async def test_a_broker_bid_moves_ticks_and_every_bar_by_the_same_amount(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> None:
    service, context = await service_with_history(ctx_factory, clock)
    try:
        before = await stored_closes(context)
        memory = [bar.close for bar in service.builder.candles("H1")][-5:]
        result = await service.calibrate(2662.4, AT)
        assert result == "harga scripted disamakan ke broker: selisih +12.40"
        after = await stored_closes(context)
        assert [round(c - b, 2) for c, b in zip(after, before, strict=True)] == [12.4] * len(before)
        assert [bar.close for bar in service.builder.candles("H1")][-5:] == pytest.approx(
            [close + 12.4 for close in memory]
        )
        clock.advance(1)
        await service.on_tick(tick(2651.0, clock.now()))
        snapshot = await service.publish_context()
        assert snapshot.bid == pytest.approx(2663.4) and snapshot.price_offset == 12.4
        # A second calibration moves by the difference only; reset goes back to the feed's own.
        assert (await service.calibrate(2660.4, clock.now())).endswith("selisih +9.40")
        assert await service.calibrate(None, clock.now()) == "harga kembali ke harga asli scripted"
        assert await stored_closes(context) == pytest.approx(before)
    finally:
        await context.close()


async def test_no_rebasing_for_a_broker_feed_or_during_a_signal(
    ctx_factory: Callable[..., AppContext], clock: ManualClock, settings: Settings
) -> None:
    service, context = await service_with_history(ctx_factory, clock)
    try:
        signal = candidate(settings)  # valid until 90 minutes after AT
        await context.ops.kv_set(KV_ENGINE, EngineState(last_signal=signal).to_json(), AT)
        assert await service.calibrate(2662.4, AT) == (
            "ada sinyal aktif: samakan harga setelah sinyal itu selesai"
        )
        await service.on_tick(tick(2650.0, AT, proxy=False, source="mt5"))
        assert await service.calibrate(2662.4, AT) == (
            "harga sudah dari broker (mt5): tidak perlu disamakan"
        )
    finally:
        await context.close()


async def test_the_dashboard_and_telegram_get_the_market_services_answer(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> None:
    service, context = await service_with_history(ctx_factory, clock)
    stop = asyncio.Event()

    async def market_side() -> None:  # what the market service's event consumer does
        while not stop.is_set():
            for message in await context.bus.read(STREAM_EVENTS, "m", "m", block_ms=50):
                await service.handle_event(message)

    task = asyncio.create_task(market_side())
    try:
        answer = await request_calibration(context, 2662.4, by="test", wait_seconds=3)
        assert answer == "harga scripted disamakan ke broker: selisih +12.40"
        stored = await context.ops.kv_get(KV_PRICE_OFFSET)
        assert stored is not None and stored["offset"] == 12.4
        assert Rebase.from_json(stored).applies("scripted")  # survives a restart
    finally:
        stop.set()
        await task
        await context.close()

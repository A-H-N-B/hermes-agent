"""Paper tracker service: signal -> fill on the next quote -> TP1 alert -> close -> engine events,
plus source filtering, reversal, restart catch-up from stored ticks and unfilled signals."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_EVENTS, STREAM_NOTIFY, Message
from gold_agent.core.clock import ManualClock
from gold_agent.models import Signal, Tick
from gold_agent.services.paper import PaperService
from gold_agent.signals.engine import KV_PAPER_DAILY
from tests.test_paper import NOW, buy


def tick(minutes: float, bid: float, source: str = "mt5") -> Tick:
    ts = NOW + timedelta(minutes=minutes)
    return Tick(ts=ts, bid=bid, ask=bid + 0.2, source=source, received_at=ts)


def msg(payload: Any) -> Message:
    data = payload if isinstance(payload, dict) else payload.model_dump(mode="json")
    return Message(id="1-0", stream="x", data=data)


async def drain(ctx: AppContext, stream: str) -> list[dict[str, Any]]:
    return [m.data for m in await ctx.bus.read(stream, f"t-{stream}", "t", count=100, block_ms=0)]


@pytest.fixture
async def paper(ctx: AppContext, clock: ManualClock) -> PaperService:
    clock.set(NOW)
    service = PaperService(ctx)
    await service.load()
    return service


async def feed(service: PaperService, clock: ManualClock, *ticks: Tick) -> None:
    for item in ticks:
        clock.set(item.ts)
        await service.handle_tick(msg(item))


async def test_signal_is_filled_managed_and_reported(
    paper: PaperService, ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    signal = buy(settings)
    await ctx.signals.save_signal(signal)
    await paper.handle_signal(msg(signal))
    await feed(paper, clock, tick(0, 2649.9, source="binance_paxg"))  # other feed: ignored
    assert signal.signal_id in paper.pending
    await feed(paper, clock, tick(0.1, 2649.9))
    stored = await ctx.signals.outcome(signal.signal_id)
    assert stored is not None and stored.entry_price == pytest.approx(2650.15)

    await feed(paper, clock, tick(10, 2656.6))
    alerts = await drain(ctx, STREAM_NOTIFY)
    assert [a["priority"] for a in alerts] == ["high"]
    assert alerts[0]["text"].startswith("✅ <b>TP1 tercapai</b>")
    await feed(paper, clock, tick(30, 2663.1))
    final = await ctx.signals.outcome(signal.signal_id)
    assert final is not None and final.status == "tp2"
    events = await drain(ctx, STREAM_EVENTS)
    assert events[0] == {"type": "signal.closed", "signal_id": signal.signal_id}
    assert events[1]["type"] == "paper.daily" and events[1]["r"] == pytest.approx(1.481)
    daily = await ctx.ops.kv_get(KV_PAPER_DAILY)
    assert daily is not None and daily["trades"] == 1
    closing = (await drain(ctx, STREAM_NOTIFY))[-1]
    assert closing["text"].startswith("📗 <b>Sinyal BUY ditutup</b>")
    assert signal.signal_id not in paper.positions


async def test_reversal_closes_the_open_position(
    paper: PaperService, ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    signal = buy(settings)
    await paper.handle_signal(msg(signal))
    await feed(paper, clock, tick(0, 2649.9), tick(3, 2651.0))
    await paper.handle_event(
        msg({"type": "signal.replaced", "signal_id": signal.signal_id, "by": "sig-x"})
    )
    outcome = await ctx.signals.outcome(signal.signal_id)
    assert outcome is not None and outcome.status == "cancelled"
    assert outcome.notes == ["arah berbalik"]


async def test_restart_resumes_and_replays_stored_ticks(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    signal = buy(settings)
    await ctx.signals.save_signal(signal)
    clock.set(NOW)
    first = PaperService(ctx)
    await first.load()
    await first.handle_signal(msg(signal))
    await feed(first, clock, tick(0.1, 2649.9))
    # the service is down while the market hits SL; the market service stored the ticks
    await ctx.market.insert_ticks([tick(5, 2647.0), tick(6, 2643.4)])
    clock.set(NOW + timedelta(minutes=10))
    restarted = PaperService(ctx)
    await restarted.load()
    outcome = await ctx.signals.outcome(signal.signal_id)
    assert outcome is not None and outcome.status == "sl"
    assert outcome.closed_at == NOW + timedelta(minutes=6)
    assert restarted.positions == {}


async def test_signal_without_any_quote_is_recorded_as_unfilled(
    paper: PaperService, ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    signal: Signal = buy(settings)
    await paper.handle_signal(msg(signal))
    clock.set(signal.valid_until_utc + timedelta(seconds=1))
    await paper.sweep()
    outcome = await ctx.signals.outcome(signal.signal_id)
    assert outcome is not None and outcome.status == "cancelled" and outcome.r_multiple is None
    assert outcome.notes == ["tidak tereksekusi: tidak ada harga dari sumber"]
    assert (await drain(ctx, STREAM_EVENTS))[-1] == {
        "type": "signal.closed",
        "signal_id": signal.signal_id,
    }

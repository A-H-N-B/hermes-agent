"""The accuracy book never makes a live caller wait (signals/accuracy.py): on the wall clock a
missing or stale result starts a rebuild in the background and the caller gets what there is."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.dashboard.data import DashboardData
from gold_agent.dashboard.live import LiveHub
from gold_agent.models import Candle, SignalComponents, SignalState
from gold_agent.signals.accuracy import REFRESH, AccuracyBook
from tests.builders import market
from tests.conftest import T0


def bars(n: int) -> list[Candle]:
    start = T0 - timedelta(minutes=15 * n)
    return [
        Candle(
            source="mt5",
            tf="M15",
            open_time=start + timedelta(minutes=15 * i),
            close_time=start + timedelta(minutes=15 * (i + 1)),
            open=2650.0 + i % 7,
            high=2652.0 + i % 7,
            low=2648.0 + i % 7,
            close=2650.5 + i % 7,
            complete=True,
        )
        for i in range(n)
    ]


async def test_live_callers_get_what_there_is_while_a_rebuild_runs(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> None:
    context = ctx_factory()
    await context.db.create_all()
    try:
        await context.market.upsert_candles(bars(600))
        book = AccuracyBook(context, background=True)
        assert await book.accuracy("M15", source="mt5") is None  # first time: not waited for
        await asyncio.gather(*book._pending.values())  # pyright: ignore[reportPrivateUsage]
        first = await book.accuracy("M15", source="mt5")
        assert first is not None
        clock.advance(REFRESH.total_seconds() + 60)
        assert await book.accuracy("M15", source="mt5") is first  # stale, served at once
        assert "M15" in book._pending  # pyright: ignore[reportPrivateUsage]
        await asyncio.gather(*book._pending.values())  # pyright: ignore[reportPrivateUsage]
        assert await book.accuracy("M15", source="mt5") is not first  # the rebuilt one
        inline = AccuracyBook(context)  # replays and tests: computed in line, deterministic
        assert await inline.accuracy("M15", source="mt5") is not None
    finally:
        await context.close()


async def test_the_dashboard_shows_the_engine_state_pushed_every_evaluation(
    ctx_factory: Callable[..., AppContext],
) -> None:

    def state(minutes: int, score: float) -> SignalState:
        parts = SignalComponents(N=score, M=0, T=0, P=0, session_factor=1)
        return SignalState(
            ts=T0 + timedelta(minutes=minutes),
            action="WAIT",
            composite_score=score,
            confidence=40,
            components=parts,
        )

    context = ctx_factory()
    await context.db.create_all()
    try:
        await context.signals.save_state(state(0, 0.10))  # stored: only on a real change
        hub = LiveHub()
        data = DashboardData(context, hub)
        assert (await data.state())["state"]["composite_score"] == 0.10
        hub.last_state = state(1, 0.14).model_dump(mode="json")  # pushed 5 s later
        assert (await data.state())["state"]["composite_score"] == 0.14
        hub.last_state = state(-5, 0.30).model_dump(mode="json")  # older than the stored one
        assert (await data.state())["state"]["composite_score"] == 0.10
    finally:
        await context.close()


async def test_the_dashboard_serves_the_pushed_context_not_the_minute_old_stored_one(
    ctx_factory: Callable[..., AppContext],
) -> None:
    context = ctx_factory()
    await context.db.create_all()
    try:
        await context.market.save_context(market(2650.0, ts=T0))  # stored once a minute
        hub = LiveHub()
        data = DashboardData(context, hub)
        assert (await data.state())["context"]["mid"] == 2650.0
        pushed = market(2655.5, ts=T0 + timedelta(seconds=5)).model_dump(mode="json")
        await hub.on_context(pushed)  # every few seconds over the bus
        assert (await data.state())["context"]["mid"] == 2655.5
    finally:
        await context.close()


async def test_one_book_serves_the_signal_service_and_the_dashboard(
    ctx_factory: Callable[..., AppContext],
) -> None:
    context = ctx_factory()
    await context.db.create_all()
    try:
        await context.market.upsert_candles(bars(600))
        shown = AccuracyBook.shared(context)  # the dashboard: shows accuracy only
        first = await shown.accuracy("M15", source="mt5")
        assert first is not None and "meta" not in first
        learner = AccuracyBook.shared(context, learn=True)  # the signal service joins
        assert learner is shown
        learnt = await learner.accuracy("M15", source="mt5")
        assert learnt is not None and "meta" in learnt  # rebuilt once with the model
        assert await AccuracyBook.shared(context).accuracy("M15", source="mt5") is learnt
    finally:
        await context.close()

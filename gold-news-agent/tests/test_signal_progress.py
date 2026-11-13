"""A signal's life after it is given (signals/progress.py): its levels never change; the price
brings it into the entry zone, reaches TP1, TP2 and TP3 (which ends it), or breaches the SL,
which makes it INVALID at once. Without TP1 it expires with its validity; after TP1 it runs on."""

from __future__ import annotations

from datetime import timedelta

import httpx

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.dashboard.app import create_app
from gold_agent.dashboard.live import LiveHub
from gold_agent.models import Signal
from gold_agent.signals.engine import KV_ENGINE, EngineState
from gold_agent.signals.progress import advance, start, still_running
from tests.test_paper import NOW, buy

HOLD = timedelta(hours=24)


def walk(signal: Signal, *prices: tuple[float, float]) -> tuple[object, list[list[str]]]:
    """(bid, ask) one per minute from the signal's creation; the progress and each step's events."""
    progress, steps = start(signal), []
    for minute, (bid, ask) in enumerate(prices, start=1):
        at = signal.created_at_utc + timedelta(minutes=minute)
        progress, events = advance(progress, signal, bid=bid, ask=ask, at=at, max_hold=HOLD)
        steps.append(list(events))
    return progress, steps


def test_a_buy_reaches_its_targets_then_a_breached_sl_makes_it_invalid(settings: Settings) -> None:
    signal = buy(settings)  # entry 2649.60-2650.40, SL 2643.60, TP1 2656.60, TP2 2663.10
    progress, steps = walk(
        signal,
        (2650.0, 2650.2),  # in the zone
        (2656.6, 2656.8),  # TP1 on the bid
        (2663.0, 2663.2),  # TP2 not yet: the bid is 2663.00
        (2663.1, 2663.3),  # TP2
        (2650.0, 2650.2),  # back down: the SL is fixed, nothing happens
        (2643.6, 2643.8),  # the bid touches the SL: INVALID
        (2670.0, 2670.2),  # whatever comes after does not count
    )
    assert steps == [["entered"], ["tp1"], [], ["tp2"], [], ["sl"], []]
    assert progress.end == "sl" and progress.end_price == 2643.6  # type: ignore[attr-defined]
    assert progress.tp3_at is None  # type: ignore[attr-defined]
    state = EngineState(last_signal=signal, progress=progress)  # type: ignore[arg-type]
    assert state.active(NOW + timedelta(minutes=8)) is None  # no longer in play


def test_tp3_ends_it_and_a_sell_is_judged_on_the_ask(settings: Settings) -> None:
    signal = buy(settings)
    _, steps = walk(signal, (signal.tp3 + 0.1, signal.tp3 + 0.3))
    assert steps == [["entered", "tp1", "tp2", "tp3"]]  # a jump through every level at once
    sell = signal.model_copy(
        update={
            "action": "SELL",
            "stop_loss": 2656.4,
            "tp1": 2643.6,
            "tp2": 2637.1,
            "tp3": 2632.0,
        }
    )
    # A SELL closes on the ask: a bid at the target is not enough while the ask is above it.
    _, steps = walk(sell, (2643.5, 2643.7), (2643.4, 2643.6), (2656.3, 2656.5))
    assert steps == [[], ["entered", "tp1"], ["sl"]]


def test_without_tp1_it_expires_and_after_tp1_it_runs_on(settings: Settings) -> None:
    signal = buy(settings)
    late = signal.valid_until_utc + timedelta(seconds=1)
    quiet, events = advance(start(signal), signal, bid=2651.0, ask=2651.2, at=late, max_hold=HOLD)
    assert events == ["expired"] and quiet.end == "expired"  # never came into the zone
    hit, _ = advance(
        start(signal), signal, bid=2656.7, ask=2656.9, at=NOW + timedelta(minutes=1), max_hold=HOLD
    )
    assert still_running(hit, signal, late)  # past its validity, TP1 keeps it in play
    assert hit.run_until == signal.created_at_utc + HOLD
    assert not still_running(hit, signal, signal.created_at_utc + HOLD)
    assert not still_running(None, signal, late)  # never followed: its validity decides


async def test_the_dashboard_keeps_an_invalid_signal_on_the_card_for_a_while(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    signal = buy(settings)
    progress, _ = walk(signal, (2656.6, 2656.8), (2643.5, 2643.7))  # TP1, then the SL
    state = EngineState(last_signal=signal, progress=progress)  # type: ignore[arg-type]
    await ctx.ops.kv_set(KV_ENGINE, state.to_json(), NOW)
    app = create_app(ctx, LiveHub(), password=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        clock.set(NOW + timedelta(minutes=10))
        payload = (await http.get("/api/state")).json()
        assert payload["active"] is None  # INVALID: no longer a signal to act on
        assert payload["ended"]["signal_id"] == signal.signal_id
        assert payload["progress"]["end"] == "sl" and payload["progress"]["tp1_at"] is not None
        clock.set(NOW + timedelta(hours=3))
        assert (await http.get("/api/state")).json()["ended"] is None  # gone after two hours

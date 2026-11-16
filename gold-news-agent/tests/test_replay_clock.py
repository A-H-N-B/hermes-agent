"""ReplayClock: virtual time stands still while work is held, sleepers wake at exactly their
deadline, same-instant sleepers take turns, periodic waits keep a fixed grid."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

from gold_agent.core.clock import ReplayClock

START = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def seconds(clock: ReplayClock) -> float:
    return (clock.now() - START).total_seconds()


async def test_a_held_clock_stands_still() -> None:
    clock = ReplayClock(START, speed=1000, held=True)
    await asyncio.sleep(0.02)  # 20 virtual seconds if it were running
    assert clock.now() == START
    clock.release()
    await asyncio.sleep(0.02)
    assert seconds(clock) >= 20
    clock.hold()
    frozen = clock.now()
    await asyncio.sleep(0.02)
    assert clock.now() == frozen


async def test_a_sleeper_wakes_at_its_deadline_however_late_the_loop_runs_it() -> None:
    clock = ReplayClock(START, speed=1000, held=True)
    woke: list[datetime] = []

    async def emitter() -> None:
        async with clock.at(START + timedelta(seconds=5)):  # held exactly at the moment
            woke.append(clock.now())

    async def sleeper() -> None:
        await clock.sleep(6)  # plain sleep: time runs on from the deadline once it woke
        woke.append(clock.now())

    tasks = [asyncio.create_task(emitter()), asyncio.create_task(sleeper())]
    await asyncio.sleep(0)
    clock.release()
    time.sleep(0.05)  # noqa: ASYNC251 -- block the event loop for 50 virtual seconds
    await asyncio.gather(*tasks)
    assert woke[0] == START + timedelta(seconds=5)
    assert timedelta(0) <= woke[1] - (START + timedelta(seconds=6)) < timedelta(seconds=1)


async def test_sleepers_due_at_the_same_instant_take_turns() -> None:
    clock = ReplayClock(START, speed=1000)
    order: list[str] = []
    moment = START + timedelta(seconds=2)

    async def emit(name: str) -> None:
        async with clock.at(moment):
            order.append(f"{name} start")
            await asyncio.sleep(0.01)  # work with awaits inside the held block
            order.append(f"{name} end")

    await asyncio.gather(emit("first"), emit("second"))
    assert order == ["first start", "first end", "second start", "second end"]
    assert clock.now() >= moment


async def test_periodic_waits_keep_their_grid_and_hold_while_the_cycle_runs() -> None:
    clock = ReplayClock(START, speed=1000)
    stop = asyncio.Event()
    cycles: list[float] = []

    async def loop() -> None:
        await clock.sleep(3)  # starts off the grid
        while not await clock.wait(stop, 5):
            cycles.append(seconds(clock))
            await asyncio.sleep(0.01)  # 10 virtual seconds of work, if time were running
            if len(cycles) == 3:
                stop.set()

    await asyncio.wait_for(loop(), timeout=5)
    assert cycles == [5.0, 10.0, 15.0]


async def test_wait_returns_as_soon_as_the_event_is_set() -> None:
    clock = ReplayClock(START, speed=1)
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(0.01, stop.set)
    assert await asyncio.wait_for(clock.wait(stop, 3600), timeout=1) is True

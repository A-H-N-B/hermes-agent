"""Clocks. Production uses SystemClock; replay runs the same code faster on ReplayClock."""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import math
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...

    async def wait(self, event: asyncio.Event, seconds: float) -> bool:
        """Wait until `event` is set or `seconds` of clock time pass; True if it was set."""
        ...


async def _wait_real(event: asyncio.Event, seconds: float) -> bool:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(event.wait(), timeout=max(0.0, seconds))
    return event.is_set()


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def wait(self, event: asyncio.Event, seconds: float) -> bool:
        return await _wait_real(event, seconds)


_GRID_EPSILON = 1e-9  # a cycle that ran exactly on its grid point waits a whole period


class ReplayClock:
    """Plays a recorded period `speed` times faster than wall time, and stands still while the
    pipeline is busy, so processing time never eats into the recording.

    It is held while any `hold()` is outstanding (the replay holds it until the services are
    up and while its bus has unread or unacknowledged messages), while an `at()` block runs (a
    replay input being emitted) and while a periodic loop woken by `wait()` runs its cycle,
    until that task calls the clock again or ends. It never passes a pending deadline (a
    sleeper wakes at exactly its virtual time however late the event loop runs it), sleepers
    due at the same instant take turns in deadline-then-registration order, each once the
    previous one's work has settled, and periodic waits run on a fixed grid from the replay
    start (start + k * seconds), so their phase does not depend on start-up timing. Virtual
    time therefore only moves while everything is idle, and a replay makes the same decisions
    at any speed or machine load. It does not measure latency (processing takes no time).
    """

    def __init__(self, start: datetime, speed: float, *, held: bool = False) -> None:
        """`held=True` starts with one hold outstanding: the clock stays at `start` exactly
        until the matching `release()`."""
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self._start = start.astimezone(UTC)
        self._speed = speed
        self._anchor = 0.0  # virtual seconds since start: at `_anchor_wall`, or while held
        self._anchor_wall = time.monotonic()
        self._holds = int(held)
        self._running = asyncio.Event()
        if not held:
            self._running.set()
        self._deadlines: list[tuple[float, int]] = []  # heap of (virtual seconds, seq)
        self._seq = itertools.count()
        self._turn = asyncio.Event()  # set (and replaced) whenever a sleeper leaves the heap
        self._cycles: set[asyncio.Task[Any]] = set()

    def _paced(self) -> float:
        if self._holds:
            return self._anchor
        return self._anchor + (time.monotonic() - self._anchor_wall) * self._speed

    def _elapsed(self) -> float:
        paced = self._paced()
        return min(paced, self._deadlines[0][0]) if self._deadlines else paced

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._elapsed())

    def hold(self) -> None:
        if self._holds == 0:
            self._anchor = self._elapsed()
            self._running.clear()
        self._holds += 1

    def release(self) -> None:
        self._holds -= 1
        if self._holds == 0:
            self._anchor_wall = time.monotonic()
            self._running.set()

    async def sleep(self, seconds: float) -> None:
        deadline = self._elapsed() + max(0.0, seconds)  # read while this task's cycle holds
        self._end_cycle()
        await self._until(deadline, None, hold=False)

    @contextlib.asynccontextmanager
    async def at(self, moment: datetime) -> AsyncGenerator[None]:
        """Wait until `moment` (a moment already past only waits for its turn) and keep the
        clock exactly there while the block runs."""
        deadline = max(self._elapsed(), (moment - self._start).total_seconds())
        self._end_cycle()
        await self._until(deadline, None, hold=True)
        try:
            yield
        finally:
            self.release()

    async def wait(self, event: asyncio.Event, seconds: float) -> bool:
        now = self._elapsed()
        deadline = now + max(0.0, seconds)
        if seconds > 0:  # the next grid point
            deadline = (math.floor(now / seconds + _GRID_EPSILON) + 1) * seconds
        self._end_cycle()
        if not await self._until(deadline, event, hold=True):
            return True
        self._begin_cycle()
        return False

    async def _until(self, deadline: float, event: asyncio.Event | None, *, hold: bool) -> bool:
        """True at the deadline (holding the clock there when `hold`), False once `event` is
        set."""
        entry = (deadline, next(self._seq))
        heapq.heappush(self._deadlines, entry)
        events = () if event is None else (event,)
        reached = False
        try:
            await asyncio.sleep(0)
            while not any(e.is_set() for e in events):
                remaining = deadline - self._elapsed()
                if self._holds:
                    await _first_set(self._running, *events)
                elif remaining > 0 and event is not None:
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(remaining / self._speed):
                            await event.wait()
                elif remaining > 0:
                    await asyncio.sleep(remaining / self._speed)
                elif self._deadlines[0] == entry:
                    reached = True
                    if hold:
                        self.hold()  # while still the earliest: frozen at exactly the deadline
                    return True
                else:  # due, but an earlier sleeper goes first
                    await _first_set(self._turn, *events)
            return False
        finally:
            self._deadlines.remove(entry)
            heapq.heapify(self._deadlines)
            if reached and self._paced() > deadline:  # time stood here until the sleeper ran
                self._anchor = deadline
                self._anchor_wall = time.monotonic()
            self._turn.set()
            self._turn = asyncio.Event()

    def _begin_cycle(self) -> None:
        """Tie the hold taken at the deadline to the woken task's cycle."""
        task = asyncio.current_task()
        if task is None:
            self.release()
            return
        self._cycles.add(task)
        task.add_done_callback(self._cycle_done)

    def _end_cycle(self) -> None:
        task = asyncio.current_task()
        if task is not None and task in self._cycles:
            task.remove_done_callback(self._cycle_done)
            self._cycle_done(task)

    def _cycle_done(self, task: asyncio.Task[Any]) -> None:
        if task in self._cycles:
            self._cycles.discard(task)
            self.release()


async def _first_set(*events: asyncio.Event) -> None:
    waiters = [asyncio.ensure_future(event.wait()) for event in events]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()


class ManualClock:
    """Test clock: time only moves when told to (sleep advances it)."""

    def __init__(self, start: datetime) -> None:
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = moment.astimezone(UTC)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)

    async def wait(self, event: asyncio.Event, seconds: float) -> bool:
        """Waits in real time without moving the clock (periodic loops idle in tests)."""
        return await _wait_real(event, seconds)

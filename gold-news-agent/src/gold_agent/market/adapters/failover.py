"""Primary feed with fall-backs (section 18: graceful degradation, labelled by tick.source)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal

import structlog

from gold_agent.collectors.base import sleep_or_stop
from gold_agent.core.clock import Clock
from gold_agent.market.adapters.base import PriceAdapter, TickSink
from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

log = structlog.get_logger(__name__)


Outcome = Literal["stopped", "failed", "handback"]


class FailoverAdapter:
    """Runs adapters in priority order; moves on after repeated failures or a silent feed. A
    stream that delivered ticks and then dropped (Binance closes every connection after 24 h)
    is reconnected, not counted against it. On a fallback the primary is tried every
    `retry_primary_seconds` BESIDE the feed in use, which never stops for it: only once the
    primary has delivered a tick does the fallback hand back."""

    def __init__(
        self,
        adapters: Sequence[PriceAdapter],
        clock: Clock,
        *,
        max_failures: int = 3,
        silence_seconds: float = 120.0,
        retry_primary_seconds: float = 600.0,
        probe_seconds: float = 20.0,
        market_open: Callable[[datetime], bool] | None = None,
        check_seconds: float = 5.0,
    ) -> None:
        if not adapters:
            raise ValueError("FailoverAdapter needs at least one adapter")
        self._adapters = list(adapters)
        self._clock = clock
        self._max_failures = max_failures
        self._silence = silence_seconds
        self._retry_primary = retry_primary_seconds
        self._probe_seconds = probe_seconds
        self._market_open = market_open or (lambda _now: True)
        self._check = check_seconds
        self.active = 0
        self._delivered = False  # the current run produced at least one tick
        # Why no ticks are coming (shown on the dashboard), None while a feed delivers.
        self.problem: str | None = None

    def find(self, name: str) -> PriceAdapter | None:
        return next((a for a in self._adapters if a.name == name), None)

    @property
    def name(self) -> str:
        return self._adapters[self.active].name

    @property
    def proxy(self) -> bool:
        return self._adapters[self.active].proxy

    async def _run_one(
        self,
        adapter: PriceAdapter,
        emit: TickSink,
        stop: asyncio.Event,
        handback: asyncio.Event | None,
    ) -> Outcome:
        """Run until stop, until the adapter fails or falls silent, or (on a fallback) until
        the primary has proven it delivers again (`handback`)."""
        last_tick = self._clock.now()
        local_stop = asyncio.Event()
        handed = False
        self._delivered = False

        async def relay(tick: Tick) -> None:
            nonlocal last_tick
            last_tick = self._clock.now()
            self._delivered = True
            self.problem = None
            await emit(tick)

        async def watchdog() -> None:
            nonlocal last_tick
            while not local_stop.is_set() and not stop.is_set():
                if await sleep_or_stop(stop, self._check):
                    break
                now = self._clock.now()
                if not self._market_open(now):
                    last_tick = now  # closed market: silence is expected
                    continue
                if (now - last_tick).total_seconds() > self._silence:
                    log.warning("feed.silent", feed=adapter.name, seconds=self._silence)
                    self.problem = f"{adapter.name}: tidak ada harga {self._silence:.0f} dtk"
                    local_stop.set()

        async def forward_stop() -> None:
            await stop.wait()
            local_stop.set()

        async def hand_back(event: asyncio.Event) -> None:
            nonlocal handed
            await event.wait()
            handed = True
            local_stop.set()

        helpers = [asyncio.create_task(watchdog()), asyncio.create_task(forward_stop())]
        if handback is not None:
            helpers.append(asyncio.create_task(hand_back(handback)))
        try:
            await adapter.run(relay, local_stop)
        finally:
            for helper in helpers:
                helper.cancel()
        if stop.is_set():
            return "stopped"
        return "handback" if handed else "failed"

    async def _probe(self, adapter: PriceAdapter) -> bool:
        """Whether `adapter` delivers a tick within `probe_seconds`; its ticks are only looked
        at, never emitted, and a hung connection is abandoned."""
        delivered = asyncio.Event()
        halt = asyncio.Event()

        async def note(_tick: Tick) -> None:
            delivered.set()
            halt.set()

        running = asyncio.create_task(adapter.run(note, halt))
        waiting = asyncio.create_task(delivered.wait())
        try:
            await asyncio.wait(
                {running, waiting},
                timeout=self._probe_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            halt.set()
            for task in (running, waiting):
                task.cancel()
            await asyncio.gather(running, waiting, return_exceptions=True)
        return delivered.is_set()

    async def _prober(self, stop: asyncio.Event, handback: asyncio.Event) -> None:
        """On a fallback, try the primary every `retry_primary_seconds` (wall clock)."""
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._retry_primary)
                return
            primary = self._adapters[0]
            if self.active > 0 and not handback.is_set() and await self._probe(primary):
                log.info("feed.primary_back", primary=primary.name)
                handback.set()

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        failures = 0
        handback = asyncio.Event()
        prober = asyncio.create_task(self._prober(stop, handback))
        try:
            while not stop.is_set():
                adapter = self._adapters[self.active]
                watch = handback if self.active > 0 else None
                try:
                    outcome = await self._run_one(adapter, emit, stop, watch)
                except Exception as exc:
                    outcome = "failed"
                    self.problem = f"{adapter.name}: {exc}"[:200]
                    log.warning("feed.failed", feed=adapter.name, error=repr(exc)[:200])
                if outcome == "stopped":
                    return
                if outcome == "handback":
                    handback.clear()
                    self.active, failures = 0, 0
                    continue
                if self._delivered:  # it streamed, then dropped: reconnect
                    failures = 0
                failures += 1
                last = self.active + 1 == len(self._adapters)
                if failures >= self._max_failures and not last:
                    self.active += 1
                    failures = 0
                    log.warning("feed.failover", to=self._adapters[self.active].name)
                elif failures >= self._max_failures and last and self.active > 0:
                    self.active, failures = 0, 0  # every feed is down: back to the top
                if await sleep_or_stop(stop, min(30.0, 2.0 ** min(failures, 5))):
                    return
        finally:
            prober.cancel()
            await asyncio.gather(prober, return_exceptions=True)

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        """From the feed that is producing ticks first, so bars and ticks are one instrument."""
        order = [self.active, *(i for i in range(len(self._adapters)) if i != self.active)]
        for index in order:
            adapter = self._adapters[index]
            try:
                candles = await adapter.history(tf, count)
            except Exception as exc:
                log.warning("feed.history_failed", feed=adapter.name, tf=tf, error=repr(exc)[:200])
                continue
            if candles:
                if index != self.active:
                    log.info("feed.history_from_fallback", feed=adapter.name, tf=tf)
                return candles
        return []

    async def contract_size(self) -> float | None:
        return await self._adapters[self.active].contract_size()

    async def volume_step(self) -> float | None:
        return await self._adapters[self.active].volume_step()

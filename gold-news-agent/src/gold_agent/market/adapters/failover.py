"""Primary feed with fall-backs (section 18: graceful degradation, labelled by tick.source)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime

import structlog

from gold_agent.collectors.base import sleep_or_stop
from gold_agent.core.clock import Clock
from gold_agent.market.adapters.base import PriceAdapter, TickSink
from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

log = structlog.get_logger(__name__)


class FailoverAdapter:
    """Runs adapters in priority order; moves on after repeated failures or a silent feed,
    and periodically retries the primary."""

    def __init__(
        self,
        adapters: Sequence[PriceAdapter],
        clock: Clock,
        *,
        max_failures: int = 3,
        silence_seconds: float = 120.0,
        retry_primary_seconds: float = 900.0,
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
        self._market_open = market_open or (lambda _now: True)
        self._check = check_seconds
        self.active = 0

    def find(self, name: str) -> PriceAdapter | None:
        return next((a for a in self._adapters if a.name == name), None)

    @property
    def name(self) -> str:
        return self._adapters[self.active].name

    @property
    def proxy(self) -> bool:
        return self._adapters[self.active].proxy

    async def _run_one(self, adapter: PriceAdapter, emit: TickSink, stop: asyncio.Event) -> bool:
        """Run until stop (True) or until the adapter fails/falls silent (False)."""
        last_tick = self._clock.now()
        local_stop = asyncio.Event()

        async def relay(tick: Tick) -> None:
            nonlocal last_tick
            last_tick = self._clock.now()
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
                    local_stop.set()

        async def forward_stop() -> None:
            await stop.wait()
            local_stop.set()

        guard = asyncio.create_task(watchdog())
        forward = asyncio.create_task(forward_stop())
        try:
            await adapter.run(relay, local_stop)
        finally:
            guard.cancel()
            forward.cancel()
        return stop.is_set()

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        failures = 0
        switched_at = self._clock.now()
        while not stop.is_set():
            adapter = self._adapters[self.active]
            try:
                if await self._run_one(adapter, emit, stop):
                    return
                failures += 1
            except Exception as exc:
                failures += 1
                log.warning("feed.failed", feed=adapter.name, error=repr(exc)[:200])
            if failures >= self._max_failures and self.active + 1 < len(self._adapters):
                self.active += 1
                failures = 0
                switched_at = self._clock.now()
                log.warning("feed.failover", to=self._adapters[self.active].name)
            elif (
                self.active > 0
                and (self._clock.now() - switched_at).total_seconds() > self._retry_primary
            ):
                self.active = 0
                failures = 0
                switched_at = self._clock.now()
            if await sleep_or_stop(stop, min(30.0, 2.0 ** min(failures, 5))):
                return

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

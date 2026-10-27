"""Primary feed with fall-backs (section 18: graceful degradation, labelled by tick.source)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Literal

import structlog

from gold_agent.collectors.base import sleep_or_stop
from gold_agent.core.clock import Clock
from gold_agent.market.adapters.base import PriceAdapter, TickSink
from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

log = structlog.get_logger(__name__)


Outcome = Literal["stopped", "failed", "retry"]
PROBE_SILENCE = 15.0  # seconds a retried primary gets to deliver while the market is open


class FailoverAdapter:
    """Runs adapters in priority order; moves on after repeated failures or a silent feed. A
    stream that delivered ticks and then dropped (Binance closes every connection after 24 h)
    is reconnected, not counted against it. On a fallback the primary is tried again every
    `retry_primary_seconds`; when it still gives nothing, the fallback resumes at once."""

    def __init__(
        self,
        adapters: Sequence[PriceAdapter],
        clock: Clock,
        *,
        max_failures: int = 3,
        silence_seconds: float = 120.0,
        retry_primary_seconds: float = 600.0,
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
        *,
        until: datetime | None,
        silence: float,
    ) -> Outcome:
        """Run until stop, until the adapter fails or falls silent (`silence` seconds before its
        first tick, the usual limit after), or until `until` (time to try the primary again)."""
        last_tick = self._clock.now()
        local_stop = asyncio.Event()
        retry = False
        self._delivered = False

        async def relay(tick: Tick) -> None:
            nonlocal last_tick
            last_tick = self._clock.now()
            self._delivered = True
            self.problem = None
            await emit(tick)

        async def watchdog() -> None:
            nonlocal last_tick, retry
            while not local_stop.is_set() and not stop.is_set():
                if await sleep_or_stop(stop, self._check):
                    break
                now = self._clock.now()
                if until is not None and now >= until:
                    retry = True
                    local_stop.set()
                    break
                if not self._market_open(now):
                    last_tick = now  # closed market: silence is expected
                    continue
                limit = self._silence if self._delivered else silence
                if (now - last_tick).total_seconds() > limit:
                    log.warning("feed.silent", feed=adapter.name, seconds=limit)
                    self.problem = f"{adapter.name}: tidak ada harga {limit:.0f} dtk"
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
        if stop.is_set():
            return "stopped"
        return "retry" if retry else "failed"

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        failures = 0
        fallback: int | None = None  # where to return when a retried primary gives nothing
        retry_at = self._clock.now() + timedelta(seconds=self._retry_primary)
        while not stop.is_set():
            adapter = self._adapters[self.active]
            until = retry_at if self.active > 0 else None
            probing = fallback is not None and self.active == 0
            try:
                outcome = await self._run_one(
                    adapter,
                    emit,
                    stop,
                    until=until,
                    silence=min(PROBE_SILENCE, self._silence) if probing else self._silence,
                )
            except Exception as exc:
                outcome = "failed"
                self.problem = f"{adapter.name}: {exc}"[:200]
                log.warning("feed.failed", feed=adapter.name, error=repr(exc)[:200])
            if outcome == "stopped":
                return
            if outcome == "retry":
                log.info("feed.retry_primary", fallback=adapter.name)
                fallback, self.active, failures = self.active, 0, 0
                continue
            if self._delivered:  # it streamed, then dropped: reconnect
                failures, fallback = 0, None
            failures += 1
            if fallback is not None and self.active == 0:  # the primary is still down
                self.active, failures, fallback = fallback, 0, None
                retry_at = self._clock.now() + timedelta(seconds=self._retry_primary)
                continue
            last = self.active + 1 == len(self._adapters)
            if failures >= self._max_failures and not last:
                self.active += 1
                failures = 0
                retry_at = self._clock.now() + timedelta(seconds=self._retry_primary)
                log.warning("feed.failover", to=self._adapters[self.active].name)
            elif failures >= self._max_failures and last and self.active > 0:
                self.active, failures = 0, 0  # every feed is down: back to the top
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

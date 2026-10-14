"""Windows side of the MT5 bridge (ADR-011 option B): read the local MetaTrader 5 terminal and
forward ticks, symbol info, FX quotes and history to the core's Redis (over Tailscale/WireGuard).

The core consumes them with `BridgeTickAdapter` (price_feed.primary: mt5_bridge)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping

import structlog

from gold_agent.core.bus import Bus
from gold_agent.market.adapters.bridge import STREAM_BRIDGE_CANDLES, STREAM_TICKS_RAW
from gold_agent.market.adapters.mt5 import Mt5Adapter
from gold_agent.models import Tick
from gold_agent.models.common import Timeframe

log = structlog.get_logger(__name__)

INFO_EVERY_SECONDS = 600.0  # repeat symbol info so a restarted core learns it again
FX_EVERY_SECONDS = 5.0


class BridgePublisher:
    def __init__(
        self,
        adapter: Mt5Adapter,
        bus: Bus,
        *,
        history: Mapping[Timeframe, int],
        fx_symbols: Mapping[str, str],
    ) -> None:
        self._adapter = adapter
        self._bus = bus
        self._history = history
        self._fx_symbols = dict(fx_symbols)
        self.sent_ticks = 0

    async def publish_symbol_info(self) -> None:
        await self._bus.publish(
            STREAM_TICKS_RAW,
            {
                "kind": "symbol_info",
                "contract_size": await self._adapter.contract_size(),
                "volume_step": await self._adapter.volume_step(),
            },
        )

    async def publish_history(self) -> int:
        sent = 0
        for tf, count in self._history.items():
            for candle in await self._adapter.history(tf, count):
                await self._bus.publish(STREAM_BRIDGE_CANDLES, candle)
                sent += 1
        return sent

    async def publish_fx(self) -> None:
        if not self._fx_symbols:
            return
        quotes = await asyncio.to_thread(self._adapter.quotes, self._fx_symbols)
        if quotes:
            broker = {self._fx_symbols[name]: mid for name, mid in quotes.items()}
            await self._bus.publish(STREAM_TICKS_RAW, {"kind": "fx_quotes", "quotes": broker})

    async def _emit(self, tick: Tick) -> None:
        await self._bus.publish(STREAM_TICKS_RAW, tick)
        self.sent_ticks += 1

    async def _periodic(self, stop: asyncio.Event) -> None:
        since_info = 0.0
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=FX_EVERY_SECONDS)
            if stop.is_set():
                return
            await self.publish_fx()
            since_info += FX_EVERY_SECONDS
            if since_info >= INFO_EVERY_SECONDS:
                since_info = 0.0
                await self.publish_symbol_info()

    async def run(self, stop: asyncio.Event) -> None:
        await self.publish_symbol_info()
        bars = await self.publish_history()
        log.info("bridge.started", history_bars=bars)
        periodic = asyncio.create_task(self._periodic(stop))
        try:
            await self._adapter.run(self._emit, stop)
        finally:
            periodic.cancel()

"""Ticks forwarded by the Windows `mt5-bridge` over Redis (stream market.ticks.raw)."""

from __future__ import annotations

import asyncio

from gold_agent.core.bus import Bus
from gold_agent.market.adapters.base import TickSink
from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

STREAM_TICKS_RAW = "market.ticks.raw"
STREAM_BRIDGE_CANDLES = "market.bridge.candles"


class BridgeTickAdapter:
    name = "mt5"
    proxy = False

    def __init__(self, bus: Bus, *, consumer: str = "market-1") -> None:
        self._bus = bus
        self._consumer = consumer
        self._contract_size: float | None = None
        self._volume_step: float | None = None

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        while not stop.is_set():
            messages = await self._bus.read(
                STREAM_TICKS_RAW, "market", self._consumer, block_ms=1000, start="latest"
            )
            for message in messages:
                data = message.data
                if data.get("kind") == "symbol_info":
                    self._contract_size = data.get("contract_size")
                    self._volume_step = data.get("volume_step")
                else:
                    await emit(Tick.model_validate(data))
            await self._bus.ack(STREAM_TICKS_RAW, "market", [m.id for m in messages])

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        """The bridge publishes history on start-up; read whatever it left on the stream."""
        messages = await self._bus.read(
            STREAM_BRIDGE_CANDLES, f"market-history-{tf}", self._consumer, count=10_000, block_ms=0
        )
        candles = [Candle.model_validate(m.data) for m in messages if m.data.get("tf") == tf]
        return candles[-count:]

    async def contract_size(self) -> float | None:
        return self._contract_size

    async def volume_step(self) -> float | None:
        return self._volume_step

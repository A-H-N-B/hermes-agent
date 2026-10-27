"""Ticks forwarded by the Windows `mt5-bridge` over Redis (stream market.ticks.raw)."""

from __future__ import annotations

import asyncio

from gold_agent.core.bus import STREAM_BRIDGE_HISTORY, Bus
from gold_agent.market.adapters.base import TickSink
from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

STREAM_TICKS_RAW = "market.ticks.raw"
HISTORY_TAIL = 30  # snapshots looked at: every timeframe's newest is among them


class BridgeTickAdapter:
    name = "mt5"
    proxy = False

    def __init__(self, bus: Bus, *, consumer: str = "market-1") -> None:
        self._bus = bus
        self._consumer = consumer
        self._contract_size: float | None = None
        self._volume_step: float | None = None
        self._fx: dict[str, float] = {}  # broker symbol -> mid (for the synthetic DXY)

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        while not stop.is_set():
            messages = await self._bus.read(
                STREAM_TICKS_RAW, "market", self._consumer, block_ms=1000, start="latest"
            )
            for message in messages:
                data = message.data
                kind = data.get("kind")
                if kind == "symbol_info":
                    self._contract_size = data.get("contract_size")
                    self._volume_step = data.get("volume_step")
                elif kind == "fx_quotes":
                    self._fx = {str(k): float(v) for k, v in (data.get("quotes") or {}).items()}
                else:
                    await emit(Tick.model_validate(data))
            await self._bus.ack(STREAM_TICKS_RAW, "market", [m.id for m in messages])

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        """The newest snapshot the bridge left for this timeframe. It republishes them every
        10 minutes, so a core that starts or restarts later still loads full history."""
        for message in reversed(await self._bus.tail(STREAM_BRIDGE_HISTORY, HISTORY_TAIL)):
            if message.data.get("tf") == tf:
                bars = message.data.get("candles") or []
                return [Candle.model_validate(bar) for bar in bars][-count:]
        return []

    async def contract_size(self) -> float | None:
        return self._contract_size

    def quotes(self, symbols: dict[str, str]) -> dict[str, float]:
        """Latest FX mids forwarded by the bridge (logical name -> broker symbol), like MT5's."""
        return {name: self._fx[broker] for name, broker in symbols.items() if broker in self._fx}

    async def volume_step(self) -> float | None:
        return self._volume_step

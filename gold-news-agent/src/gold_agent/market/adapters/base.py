"""Price adapter interface (6.1): every feed looks the same to the market service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

TickSink = Callable[[Tick], Awaitable[None]]


class PriceAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def proxy(self) -> bool:
        """True when the instrument is a stand-in for XAUUSD (e.g. PAXG)."""
        ...

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        """Stream ticks until stop; raise on disconnect so the supervisor restarts it."""
        ...

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        """The most recent `count` bars; the last one may be incomplete."""
        ...

    async def contract_size(self) -> float | None:
        """Ounces per lot from the broker; None when the feed is not a broker."""
        ...

    async def volume_step(self) -> float | None: ...

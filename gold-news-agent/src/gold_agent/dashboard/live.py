"""Live push: bus events -> every connected browser over WebSocket ({"type", "data"} JSON)."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any

import structlog
from fastapi import WebSocket

from gold_agent.dashboard.data import context_view, news_view
from gold_agent.models import Analysis, MarketContext, Tick

log = structlog.get_logger(__name__)

SEND_TIMEOUT = 5.0  # a stalled browser is dropped instead of blocking everyone


class LiveHub:
    def __init__(self, *, tick_per_second: float = 2.0) -> None:
        self._clients: set[WebSocket] = set()
        self._tick_interval = 1.0 / tick_per_second
        self._last_tick: datetime | None = None
        # The engine's newest state from the bus: it arrives every evaluation, while the stored
        # copy only changes when the decision or the score moves noticeably.
        self.last_state: dict[str, Any] | None = None

    @property
    def clients(self) -> int:
        return len(self._clients)

    def add(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, kind: str, data: Any) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send_json({"type": kind, "data": data}), SEND_TIMEOUT)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
            with contextlib.suppress(Exception):
                await ws.close()

    # ------------------------------------------------------------------ bus payloads

    async def on_tick(self, data: dict[str, Any]) -> None:
        tick = Tick.model_validate(data)
        last = self._last_tick
        if last is not None and (tick.received_at - last).total_seconds() < self._tick_interval:
            return
        self._last_tick = tick.received_at
        await self.broadcast(
            "tick",
            {"time": int(tick.ts.timestamp()), "bid": tick.bid, "ask": tick.ask, "mid": tick.mid},
        )

    async def on_context(self, data: dict[str, Any]) -> None:
        await self.broadcast("context", context_view(MarketContext.model_validate(data)))

    async def on_analysis(self, data: dict[str, Any]) -> None:
        analysis = Analysis.model_validate(data)
        if analysis.triage.is_gold_relevant:
            await self.broadcast("news", news_view(analysis))

    async def forward(self, kind: str, data: dict[str, Any]) -> None:
        """Payloads that are already dashboard-shaped (state, signal, alert, outlook)."""
        await self.broadcast(kind, data)

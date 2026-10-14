"""Binance market-data-only endpoints (*.binance.vision): PAXGUSDT as a gold proxy, EURUSDT
as a USD-strength proxy. Combined stream of bookTicker (bid/ask) + aggTrade (last, volume)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from gold_agent.core.clock import Clock
from gold_agent.market.adapters.base import TickSink
from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

INTERVALS: dict[str, str] = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
}
_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


class BinanceStreamParser:
    """Keeps the best bid/ask from bookTicker and turns every update into a Tick."""

    def __init__(self, source: str, proxy: bool) -> None:
        self.source = source
        self.proxy = proxy
        self.bid: float | None = None
        self.ask: float | None = None

    def parse(self, raw: str | bytes, received_at: datetime) -> Tick | None:
        message: Any = json.loads(raw)
        stream = str(message.get("stream", ""))
        data: Any = message.get("data", message)
        if not isinstance(data, dict):
            return None
        if "bookTicker" in stream or ("b" in data and "a" in data and "p" not in data):
            self.bid, self.ask = float(data["b"]), float(data["a"])
            return self._tick(received_at, received_at)
        if data.get("e") in ("aggTrade", "trade"):
            price = float(data["p"])
            if self.bid is None or self.ask is None:
                self.bid = self.ask = price
            event_ms = data.get("T") or data.get("E")
            ts = datetime.fromtimestamp(int(event_ms) / 1000, UTC) if event_ms else received_at
            return self._tick(ts, received_at, last=price, volume=float(data.get("q", 0.0)))
        return None

    def _tick(
        self, ts: datetime, received_at: datetime, *, last: float | None = None, volume: float = 0.0
    ) -> Tick | None:
        if self.bid is None or self.ask is None:
            return None
        return Tick(
            ts=ts,
            bid=self.bid,
            ask=self.ask,
            last=last,
            volume=volume,
            source=self.source,
            proxy=self.proxy,
            received_at=received_at,
        )


def parse_klines(
    rows: list[list[Any]], *, source: str, tf: Timeframe, now: datetime
) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        open_time = datetime.fromtimestamp(int(row[0]) / 1000, UTC)
        close_time = open_time + timedelta(seconds=_TF_SECONDS[tf])
        candles.append(
            Candle(
                source=source,
                tf=tf,
                open_time=open_time,
                close_time=close_time,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                ticks=int(row[8]) if len(row) > 8 else 0,
                complete=close_time <= now,
            )
        )
    return candles


class BinanceAdapter:
    def __init__(
        self,
        *,
        name: str,
        symbol: str,
        ws_url: str,
        rest_url: str,
        proxy: bool,
        clock: Clock,
        client: httpx.AsyncClient,
    ) -> None:
        self.name = name
        self.proxy = proxy
        self._symbol = symbol.upper()
        self._ws_url = ws_url
        parts = urlsplit(rest_url)
        self._rest_base = f"{parts.scheme}://{parts.netloc}"
        self._clock = clock
        self._client = client
        self.parser = BinanceStreamParser(name, proxy)

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        from websockets.asyncio.client import connect

        async with connect(self._ws_url, ping_interval=20, ping_timeout=20, max_queue=1024) as ws:
            stopping = asyncio.ensure_future(stop.wait())
            try:
                while not stop.is_set():
                    receive = asyncio.ensure_future(ws.recv())
                    done, _ = await asyncio.wait(
                        {receive, stopping}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if receive not in done:
                        receive.cancel()
                        return
                    tick = self.parser.parse(receive.result(), self._clock.now())
                    if tick is not None:
                        await emit(tick)
            finally:
                stopping.cancel()

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        candles: list[Candle] = []
        end_ms: int | None = None
        while len(candles) < count:
            params: dict[str, str | int] = {
                "symbol": self._symbol,
                "interval": INTERVALS[tf],
                "limit": min(1000, count - len(candles)),
            }
            if end_ms is not None:
                params["endTime"] = end_ms
            response = await self._client.get(f"{self._rest_base}/api/v3/klines", params=params)
            response.raise_for_status()
            rows: list[list[Any]] = response.json()
            if not rows:
                break
            batch = parse_klines(rows, source=self.name, tf=tf, now=self._clock.now())
            candles = batch + candles
            end_ms = int(rows[0][0]) - 1
            if len(rows) < int(params["limit"]):
                break
        return candles[-count:]

    async def contract_size(self) -> float | None:
        return None

    async def volume_step(self) -> float | None:
        return None

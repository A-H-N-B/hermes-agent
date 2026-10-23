"""Twelve Data XAU/USD. Free plan: REST polling within ~8 credits/min; WebSocket needs a paid plan.
The REST price has no bid/ask, so spread is reported as unknown (0) and labelled."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from gold_agent.collectors.base import sleep_or_stop
from gold_agent.core.clock import Clock
from gold_agent.market.adapters.base import TickSink
from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

BASE = "https://api.twelvedata.com"
INTERVALS = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h"}
_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}


class TwelveDataError(RuntimeError):
    pass


def check_response(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise TwelveDataError(str(payload.get("message", "unknown error")))
    return payload


def parse_time_series(
    payload: dict[str, Any], *, source: str, tf: Timeframe, now: datetime
) -> list[Candle]:
    candles: list[Candle] = []
    for row in reversed(payload.get("values", [])):  # API returns newest first
        open_time = datetime.fromisoformat(row["datetime"]).replace(tzinfo=UTC)
        close_time = open_time + timedelta(seconds=_TF_SECONDS[tf])
        candles.append(
            Candle(
                source=source,
                tf=tf,
                open_time=open_time,
                close_time=close_time,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
                complete=close_time <= now,
            )
        )
    return candles


class TwelveDataAdapter:
    name = "twelvedata"
    proxy = False

    def __init__(
        self,
        *,
        api_key: str,
        symbol: str,
        poll_seconds: float,
        clock: Clock,
        client: httpx.AsyncClient,
    ) -> None:
        self._key = api_key
        self._symbol = symbol
        self._poll = poll_seconds
        self._clock = clock
        self._client = client

    async def _get(self, path: str, **params: str | int) -> Any:
        response = await self._client.get(
            f"{BASE}/{path}", params={"symbol": self._symbol, "apikey": self._key, **params}
        )
        response.raise_for_status()
        return check_response(response.json())

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        while not stop.is_set():
            payload = await self._get("price")
            now = self._clock.now()
            price = float(payload["price"])
            await emit(
                Tick(ts=now, bid=price, ask=price, last=price, source=self.name, received_at=now)
            )
            if await sleep_or_stop(stop, self._poll):
                return

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        if tf not in INTERVALS:
            return []
        payload = await self._get(
            "time_series", interval=INTERVALS[tf], outputsize=min(count, 5000), timezone="UTC"
        )
        return parse_time_series(payload, source=self.name, tf=tf, now=self._clock.now())

    async def contract_size(self) -> float | None:
        return None

    async def volume_step(self) -> float | None:
        return None

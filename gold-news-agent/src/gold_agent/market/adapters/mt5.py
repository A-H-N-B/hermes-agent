"""MetaTrader 5 (Windows only): broker ticks, history, contract size, spread.

MT5 timestamps are *broker server time* written as if it were UTC (often UTC+2/+3 following
US DST). The offset is detected from a fresh tick, or set in settings (mt5_server_utc_offset).
"""

# pyright: basic

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from gold_agent.collectors.base import sleep_or_stop
from gold_agent.core.clock import Clock
from gold_agent.market.adapters.base import TickSink
from gold_agent.models import Candle, Tick
from gold_agent.models.common import Timeframe

_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def detect_offset_hours(server_epoch: float, utc_now: datetime) -> int | None:
    """Whole-hour offset between broker server clock and UTC from a *fresh* tick."""
    hours = round((server_epoch - utc_now.timestamp()) / 3600)
    return hours if -12 <= hours <= 14 else None


def server_to_utc(server_epoch: float, offset_hours: int) -> datetime:
    return datetime.fromtimestamp(server_epoch, UTC) - timedelta(hours=offset_hours)


class Mt5Adapter:
    name = "mt5"
    proxy = False

    def __init__(
        self,
        *,
        symbol: str,
        clock: Clock,
        poll_ms: int = 100,
        offset_hours: int | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        module: Any = None,
    ) -> None:
        if module is None:
            import MetaTrader5 as module  # type: ignore[import-not-found,no-redef]
        self._mt5 = module
        self._symbol = symbol
        self._clock = clock
        self._poll = poll_ms / 1000
        self._offset = offset_hours
        self._fixed_offset = offset_hours is not None
        self._credentials = (login, password, server)
        self._initialised = False
        self._last_epoch: float | None = None

    def _ensure(self) -> None:
        if self._initialised:
            return
        login, password, server = self._credentials
        kwargs: dict[str, Any] = {}
        if login:
            kwargs = {"login": int(login), "password": password, "server": server}
        if not self._mt5.initialize(**kwargs):
            raise ConnectionError(f"MT5 initialize failed: {self._mt5.last_error()}")
        if not self._mt5.symbol_select(self._symbol, True):
            raise ValueError(f"MT5 symbol not available: {self._symbol}")
        self._initialised = True

    def read_tick(self) -> Tick | None:
        """Latest tick in UTC. Until the server offset is known, returns None.

        A tick whose timestamp changed since the previous read has just arrived, so it is
        fresh and reveals the offset; a stale tick (closed market) never re-detects it.
        """
        self._ensure()
        raw = self._mt5.symbol_info_tick(self._symbol)
        if raw is None or not raw.bid or not raw.ask:
            return None
        now = self._clock.now()
        epoch = raw.time_msc / 1000 if getattr(raw, "time_msc", 0) else float(raw.time)
        if not self._fixed_offset and self._last_epoch is not None and epoch != self._last_epoch:
            detected = detect_offset_hours(epoch, now)
            if detected is not None:
                self._offset = detected
        self._last_epoch = epoch
        if self._offset is None:
            return None
        return Tick(
            ts=server_to_utc(epoch, self._offset),
            bid=float(raw.bid),
            ask=float(raw.ask),
            last=float(raw.last) if getattr(raw, "last", 0) else None,
            volume=float(getattr(raw, "volume_real", 0.0) or 0.0),
            source=self.name,
            received_at=now,
        )

    async def run(self, emit: TickSink, stop: asyncio.Event) -> None:
        last_key: tuple[float, float, datetime] | None = None
        while not stop.is_set():
            tick = await asyncio.to_thread(self.read_tick)
            if tick is not None:
                key = (tick.bid, tick.ask, tick.ts)
                if key != last_key:
                    last_key = key
                    await emit(tick)
            if await sleep_or_stop(stop, self._poll):
                return

    def _history_sync(self, tf: Timeframe, count: int) -> list[Candle]:
        self._ensure()
        timeframe = getattr(self._mt5, f"TIMEFRAME_{tf}")
        rates = self._mt5.copy_rates_from_pos(self._symbol, timeframe, 0, count)
        if rates is None:
            return []
        now = self._clock.now()
        candles: list[Candle] = []
        for row in rates:
            open_time = server_to_utc(float(row["time"]), self._offset or 0)
            close_time = open_time + timedelta(seconds=_TF_SECONDS[tf])
            candles.append(
                Candle(
                    source=self.name,
                    tf=tf,
                    open_time=open_time,
                    close_time=close_time,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["tick_volume"]),
                    complete=close_time <= now,
                )
            )
        return candles

    async def history(self, tf: Timeframe, count: int) -> list[Candle]:
        for _ in range(300):  # up to ~30s for a fresh tick to reveal the server offset
            if self._offset is not None:
                break
            await asyncio.to_thread(self.read_tick)
            await asyncio.sleep(0.1)
        if self._offset is None:
            raise RuntimeError(
                "MT5 server UTC offset unknown; set price_feed.mt5_server_utc_offset"
            )
        return await asyncio.to_thread(self._history_sync, tf, count)

    async def contract_size(self) -> float | None:
        await asyncio.to_thread(self._ensure)
        info = self._mt5.symbol_info(self._symbol)
        return float(info.trade_contract_size) if info else None

    async def volume_step(self) -> float | None:
        await asyncio.to_thread(self._ensure)
        info = self._mt5.symbol_info(self._symbol)
        return float(info.volume_step) if info else None

    def quotes(self, symbols: dict[str, str]) -> dict[str, float]:
        """Mid prices for the DXY component pairs (logical name -> broker symbol)."""
        self._ensure()
        result: dict[str, float] = {}
        for logical, broker_symbol in symbols.items():
            self._mt5.symbol_select(broker_symbol, True)
            tick = self._mt5.symbol_info_tick(broker_symbol)
            if tick is not None and tick.bid and tick.ask:
                result[logical] = (float(tick.bid) + float(tick.ask)) / 2
        return result

"""Re-basing a proxy feed onto the user's broker prices (Binance PAXG -> the broker's XAUUSD).

PAXG is a gold token: it moves with spot gold but trades a few dollars apart, a premium that
drifts slowly. When the user has no MT5 feed, every price the agent shows (and trades its paper
signals on) can be moved by one offset so it reads like their broker: they type the bid their
broker shows, the market service compares it with the feed's bid at that moment and from then on
adds the difference to every tick and bar of that feed. Stored and in-memory bars move with it,
so history and new bars stay one series and the indicators see no jump. Not for a broker feed
(MT5 prices are the broker's already), and not while a signal is active: its levels and its
paper trade were set in the old prices.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from gold_agent.core.bus import STREAM_EVENTS
from gold_agent.market.candles import shifted
from gold_agent.models import Candle, Tick

if TYPE_CHECKING:
    from gold_agent.core.app import AppContext

KV_PRICE_OFFSET = "price_offset"  # Rebase.to_json() + the last request and its result
REMEMBER = timedelta(minutes=5)  # raw quotes kept to match a broker price typed a moment ago
POLL = 0.25  # seconds between looks for the market service's answer


async def request_calibration(
    ctx: AppContext, broker: float | None, *, by: str, wait_seconds: float = 5.0
) -> str:
    """Ask the market service (which holds the live feed) to re-base onto `broker` (None: back
    to the feed's own prices) and return its answer, or a note when it takes longer."""
    request = uuid.uuid4().hex[:12]
    await ctx.bus.publish(
        STREAM_EVENTS,
        {
            "type": "price.calibrate",
            "broker_price": broker,
            "at": ctx.clock.now().isoformat(),
            "request": request,
            "by": by,
        },
    )
    for _ in range(max(1, round(wait_seconds / POLL))):
        stored = await ctx.ops.kv_get(KV_PRICE_OFFSET)
        if stored and stored.get("request") == request:
            return str(stored.get("result"))
        await asyncio.sleep(POLL)
    return "permintaan terkirim; baris harga berubah dalam beberapa detik bila berhasil"


@dataclass
class Rebase:
    source: str | None = None  # the proxy feed the offset belongs to
    offset: float = 0.0  # added to every price of that feed
    at: datetime | None = None  # when it was set
    broker: float | None = None  # the broker bid it was set from
    _recent: deque[tuple[datetime, float]] = field(
        default_factory=lambda: deque[tuple[datetime, float]](maxlen=3000)
    )

    def applies(self, source: str) -> bool:
        return self.offset != 0.0 and source == self.source

    def tick(self, tick: Tick) -> Tick:
        """Remember the raw bid, then move a proxy tick onto the broker's prices."""
        if tick.proxy:
            self._recent.append((tick.received_at, tick.bid))
            while self._recent and tick.received_at - self._recent[0][0] > REMEMBER:
                self._recent.popleft()
        if not tick.proxy or not self.applies(tick.source):
            return tick
        last = tick.last + self.offset if tick.last is not None else None
        return tick.model_copy(
            update={"bid": tick.bid + self.offset, "ask": tick.ask + self.offset, "last": last}
        )

    def candles(self, candles: list[Candle]) -> list[Candle]:
        return [shifted(c, self.offset) if self.applies(c.source) else c for c in candles]

    def raw_bid_at(self, moment: datetime) -> float | None:
        """The feed's own bid at `moment` (the last one at or before it, at most REMEMBER old)."""
        before = [bid for ts, bid in self._recent if ts <= moment]
        if before:
            return before[-1]
        return self._recent[0][1] if self._recent else None

    def note(self) -> str | None:
        if not self.offset or self.at is None:
            return None
        return f"harga proxy disamakan ke broker: selisih {self.offset:+.2f}"

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "offset": self.offset,
            "at": self.at.isoformat() if self.at else None,
            "broker": self.broker,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> Rebase:
        if not data:
            return cls()
        at = data.get("at")
        return cls(
            source=data.get("source"),
            offset=float(data.get("offset") or 0.0),
            at=datetime.fromisoformat(at) if at else None,
            broker=data.get("broker"),
        )

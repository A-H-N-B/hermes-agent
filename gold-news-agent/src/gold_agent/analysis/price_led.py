"""Price-led detection (7.9): a 1-minute move beyond k x ATR(M1), or a spread spike, raises a
volatility alert and a hunt for its cause — prices often move before free headlines arrive."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from gold_agent.models import MarketContext


@dataclass(frozen=True)
class VolatilitySpike:
    ts: datetime
    kind: Literal["move", "spread"]
    direction: Literal["up", "down", "none"]
    move: float
    threshold: float
    price: float
    spread: float | None


class PriceLedDetector:
    def __init__(self, *, k: float, cooldown_minutes: int, spread_multiple: float) -> None:
        self._k = k
        self._cooldown = timedelta(minutes=cooldown_minutes)
        self._spread_multiple = spread_multiple
        self._mids: deque[tuple[datetime, float]] = deque()
        self._last: dict[str, datetime] = {}

    def _cooling(self, key: str, now: datetime) -> bool:
        last = self._last.get(key)
        return last is not None and now - last < self._cooldown

    def update(self, context: MarketContext) -> list[VolatilitySpike]:
        if not context.market_open or context.stale or context.mid is None:
            return []
        now = context.ts
        self._mids.append((now, context.mid))
        while self._mids and now - self._mids[0][0] > timedelta(minutes=5):
            self._mids.popleft()
        spikes: list[VolatilitySpike] = []
        reference = next(
            (mid for ts, mid in reversed(self._mids) if now - ts >= timedelta(seconds=55)), None
        )
        if reference is not None and context.atr_m1:
            move = context.mid - reference
            threshold = self._k * context.atr_m1
            direction: Literal["up", "down"] = "up" if move > 0 else "down"
            key = f"move:{direction}"
            if abs(move) > threshold and not self._cooling(key, now):
                self._last[key] = now
                spikes.append(
                    VolatilitySpike(
                        now, "move", direction, move, threshold, context.mid, context.spread
                    )
                )
        if (
            context.spread is not None
            and context.spread_median_30m
            and context.spread > self._spread_multiple * context.spread_median_30m
            and not self._cooling("spread", now)
        ):
            self._last["spread"] = now
            spikes.append(
                VolatilitySpike(
                    now,
                    "spread",
                    "none",
                    context.spread,
                    self._spread_multiple * context.spread_median_30m,
                    context.mid,
                    context.spread,
                )
            )
        return spikes

"""Clocks. Production uses SystemClock; replay runs the same code faster on ReplayClock."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ReplayClock:
    """Starts at `start` and runs `speed` times faster than wall time."""

    def __init__(self, start: datetime, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self._start = start.astimezone(UTC)
        self._origin = time.monotonic()
        self._speed = speed

    def now(self) -> datetime:
        elapsed = (time.monotonic() - self._origin) * self._speed
        return self._start + timedelta(seconds=elapsed)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds) / self._speed)


class ManualClock:
    """Test clock: time only moves when told to (sleep advances it)."""

    def __init__(self, start: datetime) -> None:
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = moment.astimezone(UTC)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)

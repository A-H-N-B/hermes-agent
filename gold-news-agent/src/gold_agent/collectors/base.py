"""Shared collector plumbing: exponential backoff with jitter, stop-aware sleeping."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from gold_agent.models import NewsItem

Emit = Callable[[NewsItem], Awaitable[object]]


class Runnable(Protocol):
    name: str

    async def run(self, stop: asyncio.Event) -> None: ...


@dataclass
class Backoff:
    base: float = 2.0
    maximum: float = 300.0
    factor: float = 2.0
    jitter: float = 0.2
    attempt: int = 0
    rng: random.Random = field(default_factory=random.Random)

    def next_delay(self) -> float:
        delay = min(self.maximum, self.base * self.factor**self.attempt)
        self.attempt += 1
        return delay * self.rng.uniform(1 - self.jitter, 1 + self.jitter)

    def reset(self) -> None:
        self.attempt = 0


async def sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    """Sleep up to `seconds`; returns True when stop was requested meanwhile."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))
    except TimeoutError:
        return False
    return True

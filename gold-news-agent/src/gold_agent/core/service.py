"""Service supervision: restart crashed services with backoff, heartbeats, graceful shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)

Heartbeat = Callable[[str], Awaitable[None]]


class Service(Protocol):
    name: str

    async def run(self, stop: asyncio.Event) -> None: ...


async def run_with_restart(
    service: Service, stop: asyncio.Event, max_backoff: float = 60.0
) -> None:
    """Run a service until stop; restart it with exponential backoff when it crashes or exits."""
    backoff = 1.0
    while not stop.is_set():
        try:
            await service.run(stop)
            if stop.is_set():
                return
            log.warning("service.exited", service=service.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("service.crashed", service=service.name, restart_in=backoff)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        backoff = min(backoff * 2, max_backoff)


async def _beat(
    names: Sequence[str], heartbeat: Heartbeat, stop: asyncio.Event, every: float
) -> None:
    while not stop.is_set():
        for name in names:
            try:
                await heartbeat(name)
            except Exception:
                log.exception("service.heartbeat_failed", service=name)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every)


async def supervise(
    services: Sequence[Service],
    stop: asyncio.Event,
    *,
    heartbeat: Heartbeat | None = None,
    heartbeat_seconds: float = 30.0,
    max_backoff: float = 60.0,
) -> None:
    tasks = [
        asyncio.create_task(run_with_restart(s, stop, max_backoff), name=s.name) for s in services
    ]
    if heartbeat is not None:
        tasks.append(
            asyncio.create_task(
                _beat([s.name for s in services], heartbeat, stop, heartbeat_seconds)
            )
        )
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()


def install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl+C raises KeyboardInterrupt
            loop.add_signal_handler(sig, stop.set)

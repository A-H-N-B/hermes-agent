"""Fire drill (Phase 7 bar: "alert kesehatan teruji dengan mematikan sumber secara sengaja"):
the real collector, watchdog and notifier run together; a verified feed is killed mid-run and
brought back, and both the alert and the all-clear must reach Telegram."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.ops.watchdog import Watchdog
from gold_agent.services.collector import CollectorService
from gold_agent.services.notifier import NotifierService
from gold_agent.services.watchdog import WatchdogService
from tests.conftest import T0, make_source
from tests.test_source_check import rss


class Telegram:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self._changed = asyncio.Condition()

    async def send(self, text: str) -> None:
        async with self._changed:
            self.sent.append(text)
            self._changed.notify_all()

    async def received(self, fragment: str) -> str:
        async with self._changed:
            await self._changed.wait_for(lambda: any(fragment in t for t in self.sent))
        return next(t for t in self.sent if fragment in t)


def drill_settings(settings: Settings) -> Settings:
    """Production config, only faster: poll every second, back off in milliseconds."""
    return settings.model_copy(
        update={
            "http": settings.http.model_copy(update={"min_poll_seconds": 1}),
            "collectors": settings.collectors.model_copy(
                update={
                    "backoff_base_seconds": 0.05,
                    "backoff_max_seconds": 0.1,
                    "health_flush_seconds": 0.05,
                }
            ),
            "watchdog": settings.watchdog.model_copy(update={"check_seconds": 0.05}),
        }
    )


async def test_killing_a_source_alerts_telegram_and_its_return_sounds_the_all_clear(
    ctx_factory: Callable[..., AppContext], settings: Settings, tmp_path: Path
) -> None:
    ctx = ctx_factory([make_source(poll_seconds=1)], settings_override=drill_settings(settings))
    await ctx.db.create_all()
    alive = True
    served = asyncio.Event()

    def feed(request: httpx.Request) -> httpx.Response:
        if not alive:
            return httpx.Response(500)
        served.set()
        return httpx.Response(200, content=rss(("Gold steady ahead of CPI", T0)))

    async def offset(_path: str | None) -> float | None:
        return 0.001

    telegram = Telegram()
    watchdog = Watchdog(
        ctx, offset_reader=offset, disk_reader=lambda _p: (50, 100), data_dir=tmp_path
    )
    services = [
        CollectorService(ctx, transport=httpx.MockTransport(feed)),
        WatchdogService(ctx, watchdog),
        NotifierService(ctx, sender=telegram, poll=False),
    ]
    stop = asyncio.Event()
    tasks = [asyncio.create_task(service.run(stop)) for service in services]
    try:
        async with asyncio.timeout(20):
            await served.wait()
            alive = False  # the drill: the feed goes down
            alert = await telegram.received("Sumber feed bermasalah")
            served.clear()
            alive = True
            await served.wait()
            await telegram.received("Sumber feed normal kembali")
        stop.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        recorded = {n.text: n for n in await ctx.notify.recent(limit=50)}
    finally:
        stop.set()
        await ctx.close()
    assert "HTTP 500" in alert
    assert recorded[alert].priority == "high"  # tier-1 source: 🔴, delivered even in quiet hours
    assert recorded[alert].sent_at is not None

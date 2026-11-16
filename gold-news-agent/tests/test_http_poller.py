from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from email.utils import format_datetime

import httpx
import pytest

from gold_agent.collectors.base import Backoff
from gold_agent.collectors.http_poller import HttpPoller, RetryLater, retry_after_seconds
from gold_agent.core.clock import ManualClock
from gold_agent.models import NewsItem
from gold_agent.ops.health import HealthTracker
from gold_agent.processing.normalizer import Normalizer
from tests.conftest import T0, make_source
from tests.test_source_check import rss


def poller_for(
    handler: Callable[[httpx.Request], httpx.Response],
    emitted: list[NewsItem],
    clock: ManualClock,
    **source_overrides: object,
) -> tuple[HttpPoller, HealthTracker]:
    async def emit(item: NewsItem) -> None:
        emitted.append(item)

    health = HealthTracker("feed", clock)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    poller = HttpPoller(
        make_source(**source_overrides),
        client,
        {},
        normalizer=Normalizer(),
        emit=emit,
        health=health,
        clock=clock,
        min_poll_seconds=10,
        max_item_age_minutes=60,
        backoff=Backoff(base=1, maximum=4, jitter=0),
    )
    return poller, health


async def test_first_poll_is_backfill_and_later_items_are_live() -> None:
    clock = ManualClock(T0)
    feed = [("Old story", T0 - timedelta(minutes=5))]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=rss(*feed))

    emitted: list[NewsItem] = []
    poller, health = poller_for(handler, emitted, clock)
    assert await poller.poll_once() == 1
    assert emitted[0].backfill
    clock.advance(10)
    feed.append(("Fed cuts rates by 50bp", clock.now() - timedelta(seconds=20)))
    assert await poller.poll_once() == 1
    live = emitted[1]
    assert not live.backfill
    assert live.title == "Fed cuts rates by 50bp"
    assert live.first_seen_at == clock.now()
    assert health.state.status == "ok" and health.state.items_total == 2


async def test_stale_items_appearing_late_are_archived_as_backfill() -> None:
    clock = ManualClock(T0)
    feed: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=rss(*feed))  # type: ignore[arg-type]

    emitted: list[NewsItem] = []
    poller, _ = poller_for(handler, emitted, clock)
    await poller.poll_once()
    feed.append(("Yesterday's recap", T0 - timedelta(hours=20)))
    await poller.poll_once()
    assert emitted[-1].backfill


async def test_conditional_requests_use_validators_and_handle_304() -> None:
    clock = ManualClock(T0)
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=rss(("A", T0)), headers={"ETag": '"v1"'})

    emitted: list[NewsItem] = []
    poller, _ = poller_for(handler, emitted, clock)
    await poller.poll_once()
    assert await poller.poll_once() == 0
    assert seen_headers[1]["if-none-match"] == '"v1"'


async def test_rate_limits_honour_retry_after() -> None:
    clock = ManualClock(T0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"})

    poller, _ = poller_for(handler, [], clock)
    with pytest.raises(RetryLater) as excinfo:
        await poller.poll_once()
    assert excinfo.value.seconds == 120
    http_date = format_datetime(T0 + timedelta(seconds=90), usegmt=True)
    assert retry_after_seconds(http_date, T0) == 90


async def test_run_loop_survives_errors_and_marks_health() -> None:
    clock = ManualClock(T0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down", request=request)

    poller, health = poller_for(handler, [], clock)
    stop = asyncio.Event()
    task = asyncio.create_task(poller.run(stop))
    await asyncio.sleep(1.5)  # backoff 1s -> at least two attempts
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert calls >= 2
    assert health.state.status in ("degraded", "down")
    assert health.state.last_error and "ConnectError" in health.state.last_error

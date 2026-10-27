"""RSS/JSON poller (7.1): conditional requests, Retry-After, jittered backoff, backfill marking."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
import structlog

from gold_agent.collectors.base import Backoff, Emit, sleep_or_stop
from gold_agent.collectors.parsers import FeedItem, FormatError, parse_body
from gold_agent.config import Source, expand_url
from gold_agent.core.clock import Clock
from gold_agent.ops.health import HealthTracker
from gold_agent.ops.redaction import Redactor
from gold_agent.processing.normalizer import Normalizer

log = structlog.get_logger(__name__)


class RetryLater(Exception):
    def __init__(self, seconds: float, reason: str) -> None:
        super().__init__(reason)
        self.seconds = seconds


def retry_after_seconds(value: str | None, now: datetime, default: float = 60.0) -> float:
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return default
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def _published_key(item: FeedItem) -> datetime:
    return item.published or datetime.max.replace(tzinfo=UTC)


class HttpPoller:
    def __init__(
        self,
        source: Source,
        client: httpx.AsyncClient,
        env: dict[str, str],
        *,
        normalizer: Normalizer,
        emit: Emit,
        health: HealthTracker,
        clock: Clock,
        min_poll_seconds: float,
        max_item_age_minutes: float,
        seen_capacity: int = 5000,
        backoff: Backoff | None = None,
    ) -> None:
        self.source = source
        self.name = f"poller:{source.id}"
        self._client = client
        self._env = env
        self._normalizer = normalizer
        self._emit = emit
        self._health = health
        self._clock = clock
        self._interval = max(source.poll_seconds or min_poll_seconds, min_poll_seconds)
        self._max_age = timedelta(minutes=max_item_age_minutes)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._seen_capacity = seen_capacity
        self._validators: dict[str, str] = {}
        self._first_poll = True
        self._backoff = backoff or Backoff()
        self._redact = Redactor(env.values())

    def _remember(self, item_id: str) -> bool:
        if item_id in self._seen:
            self._seen.move_to_end(item_id)
            return False
        self._seen[item_id] = None
        if len(self._seen) > self._seen_capacity:
            self._seen.popitem(last=False)
        return True

    async def poll_once(self) -> int:
        url = expand_url(self.source.url or "", self._env)
        response = await self._client.get(url, headers=self._validators)
        now = self._clock.now()
        if response.status_code == 304:
            self._health.success(0)
            return 0
        if response.status_code in (429, 503):
            raise RetryLater(
                retry_after_seconds(response.headers.get("retry-after"), now),
                f"HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}")
        if etag := response.headers.get("etag"):
            self._validators["If-None-Match"] = etag
        if last_modified := response.headers.get("last-modified"):
            self._validators["If-Modified-Since"] = last_modified

        body = parse_body(self.source, response.content)
        emitted = 0
        for feed_item in sorted(body.items, key=_published_key):
            if not self._remember(feed_item.id):
                continue
            too_old = feed_item.published is not None and now - feed_item.published > self._max_age
            item = self._normalizer.from_feed_item(
                self.source, feed_item, first_seen_at=now, backfill=self._first_poll or too_old
            )
            if item is not None:
                await self._emit(item)
                emitted += 1
        self._first_poll = False
        self._health.success(emitted)
        return emitted

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.poll_once()
                self._backoff.reset()
                delay = self._interval
            except RetryLater as exc:
                self._health.error(f"{exc} (retry in {exc.seconds:.0f}s)")
                delay = max(exc.seconds, self._interval)
            except (httpx.HTTPError, FormatError, RuntimeError, KeyError) as exc:
                message = self._redact(f"{type(exc).__name__}: {exc}")
                self._health.error(message)
                delay = self._backoff.next_delay()
                log.warning("poller.error", source=self.source.id, error=message, retry_in=delay)
            if await sleep_or_stop(stop, delay):
                return

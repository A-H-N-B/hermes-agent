"""Collector service: collectors -> normalise -> dedup/cluster -> news.raw stream + archive."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import structlog

from gold_agent.collectors.base import sleep_or_stop
from gold_agent.collectors.http_poller import HttpPoller
from gold_agent.collectors.registry import build_news_collectors
from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_EVENTS, STREAM_NEWS_RAW, Message, consume
from gold_agent.core.service import run_with_restart
from gold_agent.models import LatencyStage, NewsCluster, NewsItem
from gold_agent.ops.health import HealthTracker
from gold_agent.processing.dedup import Deduplicator

log = structlog.get_logger(__name__)


class CollectorService:
    name = "collector"

    def __init__(
        self, ctx: AppContext, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._ctx = ctx
        self._transport = transport
        dedup = ctx.settings.news.dedup
        self._window = timedelta(minutes=dedup.window_minutes)
        self.dedup = Deduplicator(
            window_minutes=dedup.window_minutes, fuzzy_ratio=dedup.fuzzy_ratio
        )
        self.health: dict[str, HealthTracker] = {}
        self.pollers: dict[str, HttpPoller] = {}

    async def ingest(self, item: NewsItem) -> NewsCluster:
        """Cluster first and publish immediately (latency), then archive."""
        result = self.dedup.add(item)
        if result.changed:
            await self._ctx.bus.publish(STREAM_NEWS_RAW, result.cluster)
        stored = await self._ctx.news.save_item(item)
        await self._ctx.news.upsert_cluster(result.cluster)
        if stored:
            await self._ctx.ops.record_latency(
                LatencyStage(
                    ref_id=item.id,
                    source_id=item.source_id,
                    stage="first_seen",
                    ts=item.first_seen_at,
                    seconds_from_first_seen=0.0,
                )
            )
            if item.published_at is not None:
                await self._ctx.ops.record_latency(
                    LatencyStage(
                        ref_id=item.id,
                        source_id=item.source_id,
                        stage="published",
                        ts=item.published_at,
                        seconds_from_first_seen=(
                            item.published_at - item.first_seen_at
                        ).total_seconds(),
                    )
                )
        return result.cluster

    async def flush_health(self) -> None:
        for tracker in self.health.values():
            if tracker.dirty:
                tracker.dirty = False
                await self._ctx.ops.upsert_source_health(tracker.state)

    async def handle_event(self, message: Message) -> None:
        """Targeted search after an unexplained price spike: poll those sources right now."""
        if message.data.get("type") != "collector.poll_now":
            return
        for source_id in message.data.get("source_ids", []):
            poller = self.pollers.get(str(source_id))
            if poller is None:
                continue
            try:
                await poller.poll_once()
            except Exception as exc:
                log.warning("collector.poll_now_failed", source=source_id, error=repr(exc)[:200])

    async def _flush_loop(self, stop: asyncio.Event) -> None:
        every = self._ctx.settings.collectors.health_flush_seconds
        while not await sleep_or_stop(stop, every):
            try:
                await self.flush_health()
            except Exception:
                log.exception("collector.health_flush_failed")

    async def run(self, stop: asyncio.Event) -> None:
        ctx = self._ctx
        self.dedup.warm(await ctx.news.recent_clusters(ctx.clock.now() - self._window))
        async with httpx.AsyncClient(
            headers={"User-Agent": ctx.settings.http.full_user_agent()},
            timeout=ctx.settings.http.timeout_seconds,
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            collectors = build_news_collectors(ctx, client, self.ingest, self.health)
            self.pollers = {c.source.id: c for c in collectors if isinstance(c, HttpPoller)}
            if not collectors:
                log.warning(
                    "collector.no_sources",
                    hint="no enabled+verified news sources; run verify_sources and mark them",
                )
            log.info("collector.started", collectors=[c.name for c in collectors])
            tasks = [asyncio.create_task(run_with_restart(c, stop)) for c in collectors]
            flusher = asyncio.create_task(self._flush_loop(stop))
            events = asyncio.create_task(
                consume(
                    ctx.bus,
                    STREAM_EVENTS,
                    "collector",
                    "collector-1",
                    self.handle_event,
                    stop,
                    start="latest",
                )
            )
            try:
                await asyncio.gather(*tasks, flusher, events)
            finally:
                for task in [*tasks, flusher, events]:
                    task.cancel()
        # Collectors have finished their last poll: persist the final health state.
        await self.flush_health()

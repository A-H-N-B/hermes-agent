"""Build the news collectors for every enabled + verified news source."""

from __future__ import annotations

import httpx
import structlog

from gold_agent.collectors.base import Backoff, Emit, Runnable
from gold_agent.collectors.http_poller import HttpPoller
from gold_agent.collectors.telegram import TelegramCollector
from gold_agent.config import SourceKind, SourceType
from gold_agent.core.app import TELEGRAM_SESSION, AppContext
from gold_agent.ops.health import HealthTracker
from gold_agent.processing.normalizer import Normalizer

log = structlog.get_logger(__name__)


def build_news_collectors(
    ctx: AppContext,
    client: httpx.AsyncClient,
    emit: Emit,
    health: dict[str, HealthTracker],
) -> list[Runnable]:
    normalizer = Normalizer()
    collectors: list[Runnable] = []
    settings = ctx.settings
    for source in ctx.sources.production():
        if source.kind is not SourceKind.NEWS:
            continue
        tracker = health.setdefault(source.id, HealthTracker(source.id, ctx.clock))
        missing = [name for name in source.requires_env if not ctx.env.get(name)]
        if missing:
            tracker.error(f"butuh {', '.join(missing)} di .env")
            log.warning("collector.skipped", source=source.id, missing=missing)
            continue
        if source.type in (SourceType.RSS, SourceType.JSON):
            collectors.append(
                HttpPoller(
                    source,
                    client,
                    ctx.env,
                    normalizer=normalizer,
                    emit=emit,
                    health=tracker,
                    clock=ctx.clock,
                    min_poll_seconds=settings.http.min_poll_seconds,
                    max_item_age_minutes=settings.collectors.max_item_age_minutes,
                    seen_capacity=settings.collectors.seen_capacity,
                    backoff=Backoff(
                        base=settings.collectors.backoff_base_seconds,
                        maximum=settings.collectors.backoff_max_seconds,
                    ),
                )
            )
        elif source.type is SourceType.TELEGRAM:
            if not source.channels:
                tracker.error("belum ada channel di sources.yaml")
                continue
            collectors.append(
                TelegramCollector(
                    source,
                    api_id=int(ctx.env["TELEGRAM_API_ID"]),
                    api_hash=ctx.env["TELEGRAM_API_HASH"],
                    session_path=TELEGRAM_SESSION,
                    normalizer=normalizer,
                    emit=emit,
                    health=tracker,
                    clock=ctx.clock,
                )
            )
    return collectors

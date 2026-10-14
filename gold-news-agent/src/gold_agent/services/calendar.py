"""Calendar service: keeps the economic calendar and FOMC dates fresh, and schedules the
T-60 / T-30 event playbooks."""

from __future__ import annotations

import asyncio

import httpx
import structlog

from gold_agent.collectors.base import Backoff, sleep_or_stop
from gold_agent.collectors.http_poller import RetryLater
from gold_agent.core.app import AppContext
from gold_agent.econ_calendar.calibration import SurpriseCalibration
from gold_agent.econ_calendar.events import EventCatalog, load_events
from gold_agent.econ_calendar.playbook_job import PlaybookScheduler
from gold_agent.econ_calendar.sync import CalendarSync

log = structlog.get_logger(__name__)

FF_INTERVAL_SECONDS = 30 * 60  # FF rate-limits (Cloudflare 429): a few pulls per hour at most
FOMC_INTERVAL_SECONDS = 24 * 3600
PLAYBOOK_TICK_SECONDS = 30
CALIBRATION_REFRESH_SECONDS = 6 * 3600


class CalendarService:
    name = "calendar"

    def __init__(
        self, ctx: AppContext, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._ctx = ctx
        self._transport = transport

    async def run(self, stop: asyncio.Event) -> None:
        catalog = load_events()
        await asyncio.gather(self._sync_loop(catalog, stop), self._playbook_loop(catalog, stop))

    async def _sync_loop(self, catalog: EventCatalog, stop: asyncio.Event) -> None:
        ctx = self._ctx
        backoff = Backoff(base=60, maximum=3600)
        async with httpx.AsyncClient(
            headers={"User-Agent": ctx.settings.http.full_user_agent()},
            timeout=ctx.settings.http.timeout_seconds,
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            sync = CalendarSync(ctx, client, catalog)
            last_fomc = None
            while not stop.is_set():
                delay: float = FF_INTERVAL_SECONDS
                try:
                    await sync.sync_forex_factory()
                    backoff.reset()
                except RetryLater as exc:
                    delay = max(exc.seconds, FF_INTERVAL_SECONDS)
                    log.warning("calendar.rate_limited", retry_in=delay)
                except (httpx.HTTPError, ValueError) as exc:
                    delay = backoff.next_delay()
                    log.warning("calendar.sync_failed", error=ctx.redact(repr(exc))[:200])
                now = ctx.clock.now()
                if last_fomc is None or (now - last_fomc).total_seconds() > FOMC_INTERVAL_SECONDS:
                    try:
                        await sync.sync_fomc()
                        last_fomc = now
                    except (httpx.HTTPError, RetryLater) as exc:
                        log.warning("calendar.fomc_failed", error=repr(exc)[:200])
                if await sleep_or_stop(stop, delay):
                    return

    async def _playbook_loop(self, catalog: EventCatalog, stop: asyncio.Event) -> None:
        ctx = self._ctx
        calibration = SurpriseCalibration(catalog, ctx.news)
        scheduler = PlaybookScheduler(ctx, catalog, calibration)
        calibrated_at = None
        while not stop.is_set():
            now = ctx.clock.now()
            if calibrated_at is None or (now - calibrated_at).total_seconds() > (
                CALIBRATION_REFRESH_SECONDS
            ):
                await calibration.refresh()
                calibrated_at = now
            try:
                await scheduler.tick()
            except Exception:
                log.exception("playbook.tick_failed")
            if await sleep_or_stop(stop, PLAYBOOK_TICK_SECONDS):
                return

"""Calendar sync: Forex Factory weekly feed (cached, polite) and the FOMC meeting page."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import structlog

from gold_agent.collectors.http_poller import RetryLater, retry_after_seconds
from gold_agent.core.app import AppContext
from gold_agent.econ_calendar.events import EventCatalog
from gold_agent.econ_calendar.fomc import FomcMeeting, parse_fomc_calendar
from gold_agent.econ_calendar.forexfactory import parse_ff_week
from gold_agent.models import CalendarEvent
from gold_agent.storage.calendar_repo import CalendarRepo

log = structlog.get_logger(__name__)

FOMC_KV_KEY = "fomc_meetings"


def format_countdown(seconds: float) -> str:
    """'2 jam 15 mnt', '45 mnt', 'sekarang'."""
    if seconds <= 30:
        return "sekarang"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days} hari")
    if hours:
        parts.append(f"{hours} jam")
    if minutes and not days:
        parts.append(f"{minutes} mnt")
    return " ".join(parts) or "< 1 mnt"


def next_events(
    events: list[CalendarEvent], now: datetime, *, limit: int = 5, min_impact: str = "high"
) -> list[tuple[CalendarEvent, float]]:
    rank = {"high": 3, "medium": 2, "low": 1}
    upcoming = [
        e
        for e in events
        if e.scheduled_at >= now and rank.get(e.impact, 0) >= rank[min_impact] and not e.all_day
    ]
    upcoming.sort(key=lambda e: e.scheduled_at)
    return [(e, (e.scheduled_at - now).total_seconds()) for e in upcoming[:limit]]


async def load_fomc_meetings(ctx: AppContext) -> list[FomcMeeting]:
    from datetime import date

    stored = await ctx.ops.kv_get(FOMC_KV_KEY)
    if not stored:
        return []
    return [
        FomcMeeting(date.fromisoformat(m["start"]), date.fromisoformat(m["end"]), bool(m["sep"]))
        for m in stored.get("meetings", [])
    ]


class CalendarSync:
    def __init__(self, ctx: AppContext, client: httpx.AsyncClient, catalog: EventCatalog) -> None:
        self._ctx = ctx
        self._client = client
        self._catalog = catalog
        self.repo = CalendarRepo(ctx.db)
        self._validators: dict[str, dict[str, str]] = {}

    def _verified(self, source_id: str) -> bool:
        return source_id in {s.id for s in self._ctx.sources.production()}

    async def _get(self, source_id: str) -> bytes | None:
        source = self._ctx.sources.get(source_id)
        headers = self._validators.get(source_id, {})
        response = await self._client.get(source.url or "", headers=headers)
        now = self._ctx.clock.now()
        if response.status_code == 304:
            return None
        if response.status_code in (429, 503):
            raise RetryLater(
                retry_after_seconds(response.headers.get("retry-after"), now, default=900),
                f"HTTP {response.status_code}",
            )
        response.raise_for_status()
        validators: dict[str, str] = {}
        if etag := response.headers.get("etag"):
            validators["If-None-Match"] = etag
        if last_modified := response.headers.get("last-modified"):
            validators["If-Modified-Since"] = last_modified
        self._validators[source_id] = validators
        return response.content

    async def sync_forex_factory(self, source_id: str = "ff_calendar_week") -> int:
        if not self._verified(source_id):
            log.warning("calendar.unverified", source=source_id)
            return 0
        body = await self._get(source_id)
        if body is None:
            return 0
        events = parse_ff_week(body, self._catalog, source_id)
        await self.repo.upsert_events(events, self._ctx.clock.now())
        log.info("calendar.synced", source=source_id, events=len(events))
        return len(events)

    async def sync_fomc(self, source_id: str = "fomc_calendar_page") -> list[FomcMeeting]:
        if not self._verified(source_id):
            return await load_fomc_meetings(self._ctx)
        body = await self._get(source_id)
        if body is None:
            return await load_fomc_meetings(self._ctx)
        meetings = parse_fomc_calendar(body.decode("utf-8", errors="replace"))
        if meetings:
            await self._ctx.ops.kv_set(
                FOMC_KV_KEY,
                {
                    "meetings": [
                        {"start": m.start.isoformat(), "end": m.end.isoformat(), "sep": m.sep}
                        for m in meetings
                    ]
                },
                self._ctx.clock.now(),
            )
        return meetings

    async def upcoming(self, hours: float = 24) -> list[CalendarEvent]:
        now = self._ctx.clock.now()
        return await self.repo.between(now - timedelta(minutes=30), now + timedelta(hours=hours))

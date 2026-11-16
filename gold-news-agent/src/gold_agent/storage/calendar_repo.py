"""Economic calendar events and playbooks."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select

from gold_agent.models import CalendarEvent, Playbook
from gold_agent.storage.db import Database
from gold_agent.storage.tables import CalendarEventRow, PlaybookRow


class CalendarRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_events(self, events: Sequence[CalendarEvent], now: datetime) -> None:
        async with self._db.session() as session:
            for event in events:
                existing = await session.get(CalendarEventRow, event.id)
                actual = event.actual or (existing.actual if existing else None)
                stored = event.model_copy(update={"actual": actual})
                await self._db.upsert(
                    session,
                    CalendarEventRow,
                    {
                        "id": event.id,
                        "title": event.title,
                        "country": event.country,
                        "scheduled_at": event.scheduled_at,
                        "impact": event.impact,
                        "event_key": event.event_key,
                        "forecast": event.forecast,
                        "previous": event.previous,
                        "actual": actual,
                        "source_id": event.source_id,
                        "updated_at": now,
                        "payload": stored.model_dump(mode="json"),
                    },
                    ["id"],
                )

    async def between(
        self,
        start: datetime,
        end: datetime,
        *,
        countries: Sequence[str] | None = ("USD",),
        min_impact: str | None = None,
    ) -> list[CalendarEvent]:
        order = {"high": 3, "medium": 2, "low": 1}
        async with self._db.session() as session:
            query = select(CalendarEventRow).where(
                CalendarEventRow.scheduled_at >= start, CalendarEventRow.scheduled_at < end
            )
            if countries:
                query = query.where(CalendarEventRow.country.in_(list(countries)))
            rows = await session.scalars(query.order_by(CalendarEventRow.scheduled_at))
            events = [CalendarEvent.model_validate(r.payload) for r in rows]
        if min_impact:
            events = [e for e in events if order.get(e.impact, 0) >= order[min_impact]]
        return events

    async def set_actual(self, event_id: str, actual: str) -> None:
        async with self._db.session() as session:
            row = await session.get(CalendarEventRow, event_id)
            if row is None:
                return
            row.actual = actual
            payload = dict(row.payload)
            payload["actual"] = actual
            row.payload = payload

    async def save_playbook(self, playbook: Playbook) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                PlaybookRow,
                {
                    "id": playbook.id,
                    "event_id": playbook.event_id,
                    "event_key": playbook.event_key,
                    "scheduled_at": playbook.scheduled_at,
                    "created_at": playbook.created_at,
                    "sent_at": playbook.sent_at,
                    "payload": playbook.model_dump(mode="json"),
                },
                ["id"],
            )

    async def playbooks_between(self, start: datetime, end: datetime) -> list[Playbook]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(PlaybookRow)
                .where(PlaybookRow.scheduled_at >= start, PlaybookRow.scheduled_at < end)
                .order_by(PlaybookRow.scheduled_at)
            )
            return [Playbook.model_validate(r.payload) for r in rows]

    async def playbook_for(self, event_id: str) -> Playbook | None:
        async with self._db.session() as session:
            row = await session.scalar(select(PlaybookRow).where(PlaybookRow.event_id == event_id))
            return Playbook.model_validate(row.payload) if row else None

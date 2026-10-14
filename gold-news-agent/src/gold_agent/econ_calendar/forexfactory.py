"""Forex Factory weekly JSON (forecast/previous only, no actuals) -> CalendarEvent."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from gold_agent.econ_calendar.events import EventCatalog
from gold_agent.models import CalendarEvent

_IMPACT = {"high": "high", "medium": "medium", "low": "low", "holiday": "holiday"}


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def parse_ff_week(content: bytes, catalog: EventCatalog, source_id: str) -> list[CalendarEvent]:
    data: Any = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("Forex Factory payload is not a list")
    events: list[CalendarEvent] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        entry: dict[str, Any] = row
        title = str(entry.get("title", "")).strip()
        country = str(entry.get("country", "")).strip().upper()
        raw_date = str(entry.get("date", ""))
        if not title or not raw_date:
            continue
        moment = datetime.fromisoformat(raw_date)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
        # FF marks all-day/tentative items at local midnight; keep them but flag as all-day.
        local = datetime.fromisoformat(raw_date)
        all_day = (
            local.hour == 0
            and local.minute == 0
            and str(entry.get("impact", "")).lower() == "holiday"
        )
        spec = catalog.match_ff_title(title, country)
        key = f"{source_id}|{country}|{title}|{moment.isoformat()}"
        events.append(
            CalendarEvent(
                id=hashlib.sha1(key.encode()).hexdigest()[:24],  # noqa: S324
                title=title,
                country=country,
                scheduled_at=moment,
                impact=_IMPACT.get(str(entry.get("impact", "")).lower(), "unknown"),  # type: ignore[arg-type]
                forecast=_clean(entry.get("forecast")),
                previous=_clean(entry.get("previous")),
                actual=_clean(entry.get("actual")),
                event_key=spec.key if spec else None,
                source_id=source_id,
                all_day=all_day,
            )
        )
    return events

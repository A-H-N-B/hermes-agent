"""Notifications: what was sent, held (quiet hours, mute, rate limit, digest) or suppressed."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import desc, func, select, update

from gold_agent.models import Notification
from gold_agent.storage.db import Database
from gold_agent.storage.tables import NotificationRow

HELD_REASONS = ("quiet", "muted", "rate", "digest", "retry")  # "retry": Telegram failed


def _model(row: NotificationRow) -> Notification:
    return Notification(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        priority=row.priority,  # type: ignore[arg-type]
        text=row.text,
        created_at=row.created_at,
        dedupe_key=row.dedupe_key,
        sent_at=row.sent_at,
        suppressed_reason=row.suppressed_reason,
    )


class NotifyRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, notification: Notification) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                NotificationRow,
                {
                    "id": notification.id,
                    "kind": notification.kind,
                    "priority": notification.priority,
                    "created_at": notification.created_at,
                    "sent_at": notification.sent_at,
                    "dedupe_key": notification.dedupe_key,
                    "suppressed_reason": notification.suppressed_reason,
                    "text": notification.text,
                },
                ["id"],
            )

    async def seen_key(self, key: str, since: datetime) -> bool:
        """True when this dedupe key was already sent or queued since `since`."""
        async with self._db.session() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(NotificationRow)
                .where(
                    NotificationRow.dedupe_key == key,
                    NotificationRow.created_at >= since,
                    (NotificationRow.sent_at.is_not(None))
                    | (NotificationRow.suppressed_reason.in_((*HELD_REASONS, "no_telegram"))),
                )
            )
            return bool(count)

    async def held(self, since: datetime) -> list[Notification]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(NotificationRow)
                .where(
                    NotificationRow.sent_at.is_(None),
                    NotificationRow.suppressed_reason.in_(HELD_REASONS),
                    NotificationRow.created_at >= since,
                )
                .order_by(NotificationRow.created_at)
            )
            return [_model(r) for r in rows]

    async def mark(self, ids: Sequence[str], reason: str) -> None:
        if not ids:
            return
        async with self._db.session() as session:
            await session.execute(
                update(NotificationRow)
                .where(NotificationRow.id.in_(list(ids)))
                .values(suppressed_reason=reason)
            )

    async def sent_since(self, since: datetime) -> list[datetime]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(NotificationRow.sent_at).where(NotificationRow.sent_at >= since)
            )
            return [ts for ts in rows if ts is not None]

    async def recent(self, limit: int = 50) -> list[Notification]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(NotificationRow).order_by(desc(NotificationRow.created_at)).limit(limit)
            )
            return [_model(r) for r in rows]

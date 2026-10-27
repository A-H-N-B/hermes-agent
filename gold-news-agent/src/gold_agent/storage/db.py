"""Engine/session management and dialect-portable upserts."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gold_agent.config import PROJECT_ROOT
from gold_agent.storage.tables import Base

DEFAULT_SQLITE_URL = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'gold.db'}"
UPSERT_CHUNK = 1000  # rows per statement: SQLite and PostgreSQL cap bound parameters at ~32k


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


class Database:
    def __init__(self, url: str = DEFAULT_SQLITE_URL, *, echo: bool = False) -> None:
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        if self.is_sqlite and ":memory:" not in url:
            db_path = url.split("///", 1)[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {"echo": echo}
        if not self.is_sqlite:
            options.update(pool_pre_ping=True, pool_size=5, max_overflow=5)
        self.engine = create_async_engine(url, **options)
        if self.is_sqlite:
            event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        async with self.sessions() as session, session.begin():
            yield session

    async def create_all(self) -> None:
        """Dev/test schema creation. Production uses `gold-agent db upgrade` (Alembic)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()

    def insert(self, table: Any) -> Any:
        if self.dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            return pg_insert(table)
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(table)

    async def upsert(
        self,
        session: AsyncSession,
        table: Any,
        values: Mapping[str, Any],
        keys: Sequence[str],
        *,
        update: Sequence[str] | None = None,
    ) -> None:
        stmt = self.insert(table).values(**values)
        columns = [c for c in values if c not in keys] if update is None else list(update)
        if columns:
            stmt = stmt.on_conflict_do_update(
                index_elements=list(keys), set_={c: stmt.excluded[c] for c in columns}
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=list(keys))
        await session.execute(stmt)

    async def upsert_many(
        self,
        session: AsyncSession,
        table: Any,
        rows: Sequence[Mapping[str, Any]],
        keys: Sequence[str],
    ) -> None:
        """`upsert` for many rows: one multi-row statement per chunk. A key repeated in the
        batch keeps its last row, as row-by-row upserts would (PostgreSQL refuses to update
        a row twice in one statement)."""
        unique = list({tuple(row[k] for k in keys): row for row in rows}.values())
        for i in range(0, len(unique), UPSERT_CHUNK):
            batch = unique[i : i + UPSERT_CHUNK]
            stmt = self.insert(table).values(batch)
            columns = [c for c in batch[0] if c not in keys]
            stmt = stmt.on_conflict_do_update(
                index_elements=list(keys), set_={c: stmt.excluded[c] for c in columns}
            )
            await session.execute(stmt)

    async def insert_ignore(
        self, session: AsyncSession, table: Any, values: Mapping[str, Any], keys: Sequence[str]
    ) -> bool:
        stmt = self.insert(table).values(**values).on_conflict_do_nothing(index_elements=list(keys))
        result: Any = await session.execute(stmt)
        return bool(result.rowcount)

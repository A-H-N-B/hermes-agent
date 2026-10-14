"""Ticks, candles and context snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, desc, func, select

from gold_agent.models import Candle, MarketContext, Tick
from gold_agent.storage.db import Database
from gold_agent.storage.tables import CandleRow, MarketContextRow, TickRow


class MarketRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_ticks(self, ticks: Sequence[Tick]) -> None:
        if not ticks:
            return
        async with self._db.session() as session:
            session.add_all(
                TickRow(
                    ts=t.ts,
                    source=t.source,
                    bid=t.bid,
                    ask=t.ask,
                    last=t.last,
                    volume=t.volume,
                )
                for t in ticks
            )

    async def ticks_between(self, source: str | None, start: datetime, end: datetime) -> list[Tick]:
        async with self._db.session() as session:
            query = select(TickRow).where(TickRow.ts >= start, TickRow.ts < end)
            if source:
                query = query.where(TickRow.source == source)
            rows = await session.scalars(query.order_by(TickRow.ts))
            return [
                Tick(
                    ts=r.ts,
                    bid=r.bid,
                    ask=r.ask,
                    last=r.last,
                    volume=r.volume,
                    source=r.source,
                    received_at=r.ts,
                )
                for r in rows
            ]

    async def upsert_candles(self, candles: Sequence[Candle]) -> None:
        if not candles:
            return
        rows = [
            {
                "source": c.source,
                "tf": c.tf,
                "open_time": c.open_time,
                "close_time": c.close_time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "ticks": c.ticks,
            }
            for c in candles
        ]
        async with self._db.session() as session:
            await self._db.upsert_many(session, CandleRow, rows, ["source", "tf", "open_time"])

    async def candles(
        self,
        tf: str,
        start: datetime,
        end: datetime,
        *,
        source: str | None = None,
    ) -> list[Candle]:
        async with self._db.session() as session:
            query = select(CandleRow).where(
                CandleRow.tf == tf, CandleRow.open_time >= start, CandleRow.open_time < end
            )
            if source:
                query = query.where(CandleRow.source == source)
            rows = await session.scalars(query.order_by(CandleRow.open_time))
            return [
                Candle(
                    source=r.source,
                    tf=r.tf,  # type: ignore[arg-type]
                    open_time=r.open_time,
                    close_time=r.close_time,
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    volume=r.volume,
                    ticks=r.ticks,
                )
                for r in rows
            ]

    async def candle_sources(self, tf: str, since: datetime) -> dict[str, int]:
        """Bars stored per source for a timeframe (to pick one consistent history)."""
        async with self._db.session() as session:
            rows = await session.execute(
                select(CandleRow.source, func.count())
                .where(CandleRow.tf == tf, CandleRow.open_time >= since)
                .group_by(CandleRow.source)
            )
            return {source: int(count) for source, count in rows}

    async def latest_candles(
        self, tf: str, limit: int, *, source: str | None = None, before: datetime | None = None
    ) -> list[Candle]:
        """The newest `limit` bars (oldest first), optionally only those opened before `before`."""
        async with self._db.session() as session:
            query = select(CandleRow).where(CandleRow.tf == tf)
            if source:
                query = query.where(CandleRow.source == source)
            if before is not None:
                query = query.where(CandleRow.open_time < before)
            rows = list(
                await session.scalars(query.order_by(desc(CandleRow.open_time)).limit(limit))
            )
            return [
                Candle(
                    source=r.source,
                    tf=r.tf,  # type: ignore[arg-type]
                    open_time=r.open_time,
                    close_time=r.close_time,
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    volume=r.volume,
                    ticks=r.ticks,
                )
                for r in reversed(rows)
            ]

    async def save_context(self, context: MarketContext) -> None:
        async with self._db.session() as session:
            session.add(MarketContextRow(ts=context.ts, payload=context.model_dump(mode="json")))

    async def prune(self, *, ticks_before: datetime, contexts_before: datetime) -> tuple[int, int]:
        """Retention (C4.3): drop archived ticks / context snapshots older than the cut-offs.
        Candles are kept: they are small and feed the hourly profile and event studies."""
        async with self._db.session() as session:
            ticks: Any = await session.execute(delete(TickRow).where(TickRow.ts < ticks_before))
            contexts: Any = await session.execute(
                delete(MarketContextRow).where(MarketContextRow.ts < contexts_before)
            )
            return int(ticks.rowcount or 0), int(contexts.rowcount or 0)

    async def contexts_between(self, start: datetime, end: datetime) -> list[MarketContext]:
        """Stored snapshots, oldest first: exactly what the engine saw (replay / backtest)."""
        async with self._db.session() as session:
            rows = await session.scalars(
                select(MarketContextRow)
                .where(MarketContextRow.ts >= start, MarketContextRow.ts < end)
                .order_by(MarketContextRow.ts)
            )
            return [MarketContext.model_validate(r.payload) for r in rows]

    async def latest_context(self) -> MarketContext | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(MarketContextRow).order_by(desc(MarketContextRow.ts)).limit(1)
            )
            return MarketContext.model_validate(row.payload) if row else None

"""Signals, engine states, outcomes, outlooks and the hourly profile."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import desc, select

from gold_agent.models import Outlook, Signal, SignalOutcome, SignalState
from gold_agent.signals.profile import HourStats
from gold_agent.storage.db import Database
from gold_agent.storage.tables import (
    HourlyProfileRow,
    OutlookRowTable,
    SignalOutcomeRow,
    SignalRow,
    SignalStateRow,
)


class SignalRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ signals

    async def save_signal(self, signal: Signal) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                SignalRow,
                {
                    "signal_id": signal.signal_id,
                    "created_at": signal.created_at_utc,
                    "action": signal.action,
                    "setup_type": signal.setup_type,
                    "confidence": signal.confidence,
                    "composite_score": signal.composite_score,
                    "entry_low": signal.entry_low,
                    "entry_high": signal.entry_high,
                    "stop_loss": signal.stop_loss,
                    "tp1": signal.tp1,
                    "tp2": signal.tp2,
                    "tp3": signal.tp3,
                    "valid_until": signal.valid_until_utc,
                    "payload": signal.model_dump(mode="json"),
                },
                ["signal_id"],
            )

    async def get_signal(self, signal_id: str) -> Signal | None:
        async with self._db.session() as session:
            row = await session.get(SignalRow, signal_id)
            return Signal.model_validate(row.payload) if row else None

    async def signals_between(self, start: datetime, end: datetime) -> list[Signal]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SignalRow)
                .where(SignalRow.created_at >= start, SignalRow.created_at < end)
                .order_by(SignalRow.created_at)
            )
            return [Signal.model_validate(r.payload) for r in rows]

    async def latest_signals(self, limit: int = 20) -> list[Signal]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SignalRow).order_by(desc(SignalRow.created_at)).limit(limit)
            )
            return [Signal.model_validate(r.payload) for r in rows]

    # ------------------------------------------------------------------ engine states

    async def save_state(self, state: SignalState) -> None:
        async with self._db.session() as session:
            session.add(
                SignalStateRow(
                    ts=state.ts,
                    action=state.action,
                    composite_score=state.composite_score,
                    payload=state.model_dump(mode="json"),
                )
            )

    async def latest_state(self) -> SignalState | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(SignalStateRow).order_by(desc(SignalStateRow.ts)).limit(1)
            )
            return SignalState.model_validate(row.payload) if row else None

    async def states_between(self, start: datetime, end: datetime) -> list[SignalState]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SignalStateRow)
                .where(SignalStateRow.ts >= start, SignalStateRow.ts < end)
                .order_by(SignalStateRow.ts)
            )
            return [SignalState.model_validate(r.payload) for r in rows]

    # ------------------------------------------------------------------ outcomes (paper)

    async def save_outcome(self, outcome: SignalOutcome) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                SignalOutcomeRow,
                {
                    "signal_id": outcome.signal_id,
                    "status": outcome.status,
                    "r_multiple": outcome.r_multiple,
                    "opened_at": outcome.opened_at,
                    "closed_at": outcome.closed_at,
                    "payload": outcome.model_dump(mode="json"),
                },
                ["signal_id"],
            )

    async def outcome(self, signal_id: str) -> SignalOutcome | None:
        async with self._db.session() as session:
            row = await session.get(SignalOutcomeRow, signal_id)
            return SignalOutcome.model_validate(row.payload) if row else None

    async def open_outcomes(self) -> list[SignalOutcome]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SignalOutcomeRow)
                .where(SignalOutcomeRow.closed_at.is_(None))
                .order_by(SignalOutcomeRow.opened_at)
            )
            return [SignalOutcome.model_validate(r.payload) for r in rows]

    async def outcomes_between(self, start: datetime, end: datetime) -> list[SignalOutcome]:
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SignalOutcomeRow)
                .where(SignalOutcomeRow.opened_at >= start, SignalOutcomeRow.opened_at < end)
                .order_by(SignalOutcomeRow.opened_at)
            )
            return [SignalOutcome.model_validate(r.payload) for r in rows]

    # ------------------------------------------------------------------ outlooks

    async def save_outlook(self, outlook: Outlook) -> None:
        async with self._db.session() as session:
            await self._db.upsert(
                session,
                OutlookRowTable,
                {
                    "id": outlook.id,
                    "created_at": outlook.created_at,
                    "trigger": outlook.trigger[:64],
                    "payload": outlook.model_dump(mode="json"),
                },
                ["id"],
            )

    async def latest_outlook(self, trigger: str | None = None) -> Outlook | None:
        async with self._db.session() as session:
            query = select(OutlookRowTable)
            if trigger is not None:
                query = query.where(OutlookRowTable.trigger == trigger)
            row = await session.scalar(query.order_by(desc(OutlookRowTable.created_at)).limit(1))
            return Outlook.model_validate(row.payload) if row else None

    # ------------------------------------------------------------------ hourly profile

    async def save_profile(self, stats: Sequence[HourStats], now: datetime) -> None:
        async with self._db.session() as session:
            for item in stats:
                await self._db.upsert(
                    session,
                    HourlyProfileRow,
                    {
                        "key": item.key,
                        "basis": item.basis,
                        "bucket": item.bucket,
                        "samples": item.samples,
                        "mean_range": item.mean_range,
                        "median_range": item.median_range,
                        "std_range": item.std_range,
                        "pct_up": item.pct_up,
                        "avg_spread": item.avg_spread,
                        "computed_at": now,
                    },
                    ["key"],
                )

    async def profile(self) -> tuple[list[HourStats], datetime | None]:
        async with self._db.session() as session:
            rows = list(await session.scalars(select(HourlyProfileRow)))
        stats = [
            HourStats(
                basis=r.basis,  # type: ignore[arg-type]
                bucket=r.bucket,
                samples=r.samples,
                mean_range=r.mean_range,
                median_range=r.median_range,
                std_range=r.std_range,
                pct_up=r.pct_up,
                avg_spread=r.avg_spread,
            )
            for r in rows
        ]
        computed = max((r.computed_at for r in rows), default=None)
        return stats, computed

"""Wall-clock jobs that survive restarts: a job is due when its latest scheduled slot is newer than
its last recorded run (kept in kv_state). A service that was down at the slot runs it on start."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from gold_agent.storage.ops_repo import OpsRepo

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class Job:
    name: str
    zone: str
    hhmm: str
    weekday: str | None = None  # None = every day

    def _at(self, day: date) -> datetime:
        local = datetime.combine(day, time.fromisoformat(self.hhmm), tzinfo=ZoneInfo(self.zone))
        return local.astimezone(ZoneInfo("UTC"))

    def last_slot(self, now: datetime) -> datetime:
        day = now.astimezone(ZoneInfo(self.zone)).date()
        for back in range(8):
            candidate = day - timedelta(days=back)
            if self.weekday is not None and WEEKDAYS[candidate.weekday()] != self.weekday:
                continue
            slot = self._at(candidate)
            if slot <= now:
                return slot
        return self._at(day - timedelta(days=7))

    def due(self, now: datetime, last_run: datetime | None) -> bool:
        return last_run is None or last_run < self.last_slot(now)


class JobLedger:
    """Last-run times per job in kv_state (one key per job)."""

    def __init__(self, ops: OpsRepo) -> None:
        self._ops = ops

    async def last_run(self, job: Job) -> datetime | None:
        stored = await self._ops.kv_get(f"job:{job.name}")
        return datetime.fromisoformat(stored["at"]) if stored and stored.get("at") else None

    async def mark(self, job: Job, at: datetime) -> None:
        await self._ops.kv_set(f"job:{job.name}", {"at": at.isoformat()}, at)

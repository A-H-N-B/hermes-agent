"""Per-source health tracking (heartbeat, errors, item flow) for the watchdog and dashboard."""

from __future__ import annotations

from gold_agent.core.clock import Clock
from gold_agent.models import SourceHealth


class HealthTracker:
    def __init__(self, source_id: str, clock: Clock) -> None:
        self._clock = clock
        self.state = SourceHealth(source_id=source_id, status="idle", updated_at=clock.now())
        self.dirty = True

    def success(self, items: int = 0) -> None:
        now = self._clock.now()
        update: dict[str, object] = {
            "status": "ok",
            "last_success_at": now,
            "consecutive_errors": 0,
            "updated_at": now,
        }
        if items:
            update["last_item_at"] = now
            update["items_total"] = self.state.items_total + items
        self.state = self.state.model_copy(update=update)
        self.dirty = True

    def error(self, message: str) -> None:
        now = self._clock.now()
        errors = self.state.consecutive_errors + 1
        self.state = self.state.model_copy(
            update={
                "status": "degraded" if errors < 3 else "down",
                "consecutive_errors": errors,
                "last_error": message[:500],
                "last_error_at": now,
                "updated_at": now,
            }
        )
        self.dirty = True

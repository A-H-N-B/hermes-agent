"""Watchdog service: health checks every few seconds -> alerts (the notifier sends them), the clock
offset for the dashboard, and the nightly retention clean-up of old ticks, market snapshots and
engine states."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

import structlog

from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_ALERTS
from gold_agent.core.schedule import Job, JobLedger
from gold_agent.ops.watchdog import Watchdog

log = structlog.get_logger(__name__)

KV_CLOCK = "clock_offset"
MAINTENANCE = Job("maintenance", "Asia/Jakarta", "04:15")  # inside the daily break (US DST)


class WatchdogService:
    name = "watchdog"

    def __init__(self, ctx: AppContext, watchdog: Watchdog | None = None) -> None:
        self._ctx = ctx
        self.watchdog = watchdog or Watchdog(ctx)
        self.ledger = JobLedger(ctx.ops)

    async def check(self) -> int:
        ctx = self._ctx
        alerts = await self.watchdog.check()
        for index, alert in enumerate(alerts):
            try:
                await ctx.ops.save_alert(alert)
                await ctx.bus.publish(STREAM_ALERTS, alert)
            except Exception:
                for unsent in alerts[index:]:
                    self.watchdog.undo(unsent)  # raised again on the next check
                raise
            log.warning("watchdog.alert", title=alert.title, severity=alert.severity)
        offset = self.watchdog.clock_offset_seconds
        await ctx.ops.kv_set(KV_CLOCK, {"offset_seconds": offset}, ctx.clock.now())
        return len(alerts)

    async def maintenance(self) -> bool:
        ctx = self._ctx
        now = ctx.clock.now()
        if not MAINTENANCE.due(now, await self.ledger.last_run(MAINTENANCE)):
            return False
        cfg = ctx.settings.watchdog
        snapshots_before = now - timedelta(days=cfg.context_retention_days)
        ticks, contexts = await ctx.market.prune(
            ticks_before=now - timedelta(days=cfg.tick_retention_days),
            contexts_before=snapshots_before,
        )
        states = await ctx.signals.prune_states(snapshots_before)  # one row a minute otherwise
        await self.ledger.mark(MAINTENANCE, now)
        log.info("watchdog.pruned", ticks=ticks, contexts=contexts, states=states)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        every = self._ctx.settings.watchdog.check_seconds
        while not stop.is_set():
            try:
                await self.check()
                await self.maintenance()
            except Exception:
                log.exception("watchdog.check_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=every)

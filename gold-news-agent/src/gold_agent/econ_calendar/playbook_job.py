"""Playbook schedule (11.4): build at T-60 for every high-impact event with a known spec,
rebuild when the forecast appears, publish once at T-30."""

from __future__ import annotations

from datetime import timedelta

import structlog

from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_PLAYBOOKS
from gold_agent.econ_calendar.calibration import SurpriseCalibration
from gold_agent.econ_calendar.events import EventCatalog
from gold_agent.econ_calendar.playbook import build_playbook
from gold_agent.models import Playbook

log = structlog.get_logger(__name__)


class PlaybookScheduler:
    def __init__(
        self, ctx: AppContext, catalog: EventCatalog, calibration: SurpriseCalibration
    ) -> None:
        self._ctx = ctx
        self._catalog = catalog
        self._calibration = calibration

    async def tick(self) -> list[Playbook]:
        """One pass; returns the playbooks published in it."""
        ctx = self._ctx
        now = ctx.clock.now()
        outlook = ctx.settings.outlook
        build_window = timedelta(minutes=outlook.playbook_build_minutes)
        send_window = timedelta(minutes=outlook.playbook_send_minutes)
        published: list[Playbook] = []
        for event in await ctx.calendar.between(now, now + build_window):
            if event.impact != "high" or event.all_day or event.event_key is None:
                continue
            spec = self._catalog.by_key(event.event_key)
            if spec is None:
                continue
            playbook = await ctx.calendar.playbook_for(event.id)
            if playbook is None or (playbook.forecast is None and event.forecast):
                std, basis = self._calibration.std_for(spec)
                fresh = build_playbook(event, spec, surprise_std=std, z_basis=basis, now=now)
                if playbook is not None:  # keep identity and delivery state across a rebuild
                    fresh = fresh.model_copy(
                        update={"id": playbook.id, "sent_at": playbook.sent_at}
                    )
                playbook = fresh
                await ctx.calendar.save_playbook(playbook)
                log.info("playbook.built", title=event.title, forecast=playbook.forecast)
            if playbook.sent_at is None and event.scheduled_at - now <= send_window:
                playbook = playbook.model_copy(update={"sent_at": now})
                await ctx.calendar.save_playbook(playbook)
                await ctx.bus.publish(STREAM_PLAYBOOKS, playbook)
                published.append(playbook)
        return published

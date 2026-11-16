"""What becomes a Telegram notification, at which priority (🔴 high / 🟡 normal / 🔵 digest).

High-impact items pass quiet hours: signals, reversals, fast-path flashes, playbooks for
high-impact releases, high-impact news, critical health alerts and price spikes. Items that
arrive too late to act on (a restart, a backlog) are not sent at all.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Literal

from gold_agent.config import Settings
from gold_agent.models import (
    Alert,
    Analysis,
    CalendarEvent,
    Notification,
    Outlook,
    Playbook,
    ReleaseReading,
    Signal,
)
from gold_agent.models.common import Priority
from gold_agent.notify import templates

LONG_DEDUPE = timedelta(days=7)  # one notification per signal / story / playbook, ever
AgeKind = Literal["flash", "news", "alert", "outlook"]


def notification_id(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


class NotificationPolicy:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cfg = settings.notifications

    def _too_old(self, kind: AgeKind, moment: datetime, now: datetime) -> bool:
        minutes = self._cfg.max_age_minutes.get(kind)
        return minutes is not None and now - moment > timedelta(minutes=minutes)

    def dedupe_window(self, kind: str) -> timedelta:
        if kind in ("signal", "signal_update", "news", "playbook", "outlook"):
            return LONG_DEDUPE
        return timedelta(minutes=self._cfg.dedupe_minutes)

    # ------------------------------------------------------------------ builders

    def for_signal(
        self, signal: Signal, *, now: datetime, next_event: CalendarEvent | None
    ) -> Notification | None:
        if now >= signal.valid_until_utc:
            return None
        return Notification(
            id=notification_id("signal", signal.signal_id),
            kind="signal",
            priority="high",
            text=templates.render_signal(signal, now=now, next_event=next_event),
            created_at=now,
            dedupe_key=f"signal:{signal.signal_id}",
            ref_ids=signal.linked_news_ids[:1],
            first_seen_at=signal.first_seen_at,
        )

    def for_replaced(
        self, old: Signal, *, new_id: str, new_action: str, now: datetime
    ) -> Notification:
        return Notification(
            id=notification_id("replaced", old.signal_id),
            kind="signal_update",
            priority="high",
            text=templates.render_signal_replaced(old, new_id, new_action),
            created_at=now,
            dedupe_key=f"replaced:{old.signal_id}",
        )

    def for_release(self, release: ReleaseReading, *, now: datetime) -> Notification | None:
        if self._too_old("flash", release.first_seen_at, now):
            return None
        trading = self._settings.trading
        return Notification(
            id=notification_id("flash", release.event_key, release.first_seen_at.isoformat()),
            kind="flash",
            priority="high",
            text=templates.render_flash(
                release,
                now=now,
                confirm_seconds=trading.post_release_confirm_seconds,
                news_trading=trading.news_trading,
            ),
            created_at=now,
            dedupe_key=f"flash:{release.event_key}",
            ref_ids=[release.cluster_id] if release.cluster_id else [],
            first_seen_at=release.first_seen_at,
        )

    def for_analysis(self, analysis: Analysis, *, now: datetime) -> Notification | None:
        triage = analysis.triage
        if (
            not triage.is_gold_relevant
            or analysis.origin == "fast_path"  # the flash already covered it
            or triage.impact_score < self._cfg.news_min_impact
            or self._too_old("news", analysis.first_seen_at, now)
        ):
            return None
        priority: Priority = (
            "high" if triage.impact_score >= self._cfg.news_high_impact else "normal"
        )
        return Notification(
            id=notification_id("news", analysis.cluster_id),
            kind="news",
            priority=priority,
            text=templates.render_news(analysis, now=now),
            created_at=now,
            dedupe_key=f"news:{analysis.cluster_id}",
            ref_ids=[analysis.cluster_id],
            first_seen_at=analysis.first_seen_at,
        )

    def for_alert(self, alert: Alert, *, now: datetime) -> Notification | None:
        if self._too_old("alert", alert.created_at, now):
            return None
        priority: Priority
        if alert.severity == "critical" or (
            alert.kind == "volatility" and alert.severity != "info"
        ):
            priority = "high"
        elif alert.severity == "warning" or alert.kind == "recovery":
            priority = "normal"
        else:
            priority = "digest"
        kind = "volatility" if alert.kind in ("volatility", "spread") else "health"
        return Notification(
            id=notification_id("alert", alert.id),
            kind=kind,
            priority=priority,
            text=templates.render_alert(alert),
            created_at=now,
            dedupe_key=f"alert:{alert.dedupe_key}",
            ref_ids=alert.ref_ids[:1],
        )

    def for_outlook(self, outlook: Outlook, *, now: datetime) -> Notification | None:
        if not any(outlook.trigger.startswith(t) for t in self._cfg.outlook_triggers):
            return None
        if self._too_old("outlook", outlook.created_at, now):
            return None
        return Notification(
            id=notification_id("outlook", outlook.id),
            kind="outlook",
            priority="normal",
            text=templates.render_outlook(outlook),
            created_at=now,
            dedupe_key=f"outlook:{outlook.id}",
        )

    def for_playbook(self, playbook: Playbook, *, now: datetime) -> Notification | None:
        if now >= playbook.scheduled_at:
            return None
        return Notification(
            id=notification_id("playbook", playbook.event_id),
            kind="playbook",
            priority="high",
            text=templates.render_playbook(playbook, now=now),
            created_at=now,
            dedupe_key=f"playbook:{playbook.event_id}",
        )

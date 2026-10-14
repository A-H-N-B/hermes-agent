"""Delivery rules (section 15): priority, quiet hours, mute, dedupe, rate limit with merging,
digest, the 4096-character limit and notified latency.

🔴 high always goes out at once. 🟡 normal is held during quiet hours or a /mute and when the
per-minute budget is spent; 🔵 digest waits for the digest interval. Held messages are never
dropped: the next flush merges them into one summary. Every decision is stored in
`notifications`, so a restart neither loses held messages nor resends delivered ones.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from datetime import datetime, time, timedelta
from typing import Protocol

import structlog

from gold_agent.config import Settings
from gold_agent.core.clock import Clock
from gold_agent.models import LatencyStage, Notification
from gold_agent.notify.format import split_message
from gold_agent.notify.policy import NotificationPolicy, notification_id
from gold_agent.notify.templates import render_digest
from gold_agent.storage.notify_repo import NotifyRepo
from gold_agent.storage.ops_repo import OpsRepo
from gold_agent.timeutils.sessions import to_wib

log = structlog.get_logger(__name__)

KV_MUTE = "notify_mute"
HELD_LOOKBACK = timedelta(hours=24)
RATE_WINDOW = timedelta(minutes=1)


class Sender(Protocol):
    async def send(self, text: str) -> None: ...


def in_quiet_hours(moment: datetime, start: str, end: str) -> bool:
    """WIB window, which may wrap midnight (e.g. 22:00-05:00)."""
    local = to_wib(moment).time()
    begin, finish = time.fromisoformat(start), time.fromisoformat(end)
    if begin <= finish:
        return begin <= local < finish
    return local >= begin or local < finish


class Dispatcher:
    def __init__(
        self,
        settings: Settings,
        *,
        repo: NotifyRepo,
        ops: OpsRepo,
        clock: Clock,
        sender: Sender | None,
    ) -> None:
        self._settings = settings
        self._cfg = settings.notifications
        self._repo = repo
        self._ops = ops
        self._clock = clock
        self._sender = sender
        self._policy = NotificationPolicy(settings)
        self._sent: deque[datetime] = deque()
        self._last_digest_at: datetime | None = None
        self.mute_until: datetime | None = None

    async def load(self) -> None:
        now = self._clock.now()
        self._sent.extend(await self._repo.sent_since(now - RATE_WINDOW))
        stored = await self._ops.kv_get(KV_MUTE)
        if stored and stored.get("until"):
            self.mute_until = datetime.fromisoformat(stored["until"])

    # ------------------------------------------------------------------ controls

    async def mute(self, duration: timedelta | None) -> datetime | None:
        now = self._clock.now()
        self.mute_until = now + duration if duration else None
        until = self.mute_until.isoformat() if self.mute_until else None
        await self._ops.kv_set(KV_MUTE, {"until": until}, now)
        return self.mute_until

    def muted(self, now: datetime) -> bool:
        return self.mute_until is not None and now < self.mute_until

    def quiet(self, now: datetime) -> bool:
        hours = self._cfg.quiet_hours_wib
        return in_quiet_hours(now, hours.start, hours.end)

    def _rate_ok(self, now: datetime) -> bool:
        while self._sent and now - self._sent[0] >= RATE_WINDOW:
            self._sent.popleft()
        return len(self._sent) < self._cfg.max_messages_per_minute

    def _hold_reason(self, notification: Notification, now: datetime) -> str | None:
        if notification.priority == "high":
            return None
        if self.muted(now):
            return "muted"
        if (
            self.quiet(now)
            and notification.priority not in self._cfg.quiet_hours_wib.allow_priorities
        ):
            return "quiet"
        if notification.priority == "digest":
            return "digest"
        if not self._rate_ok(now):
            return "rate"
        return None

    # ------------------------------------------------------------------ intake

    async def submit(self, notification: Notification) -> str:
        """Returns what happened: sent, duplicate, no_telegram, or the hold reason."""
        now = self._clock.now()
        key = notification.dedupe_key
        if key and await self._repo.seen_key(
            key, now - self._policy.dedupe_window(notification.kind)
        ):
            await self._repo.save(
                notification.model_copy(update={"suppressed_reason": "duplicate"})
            )
            return "duplicate"
        reason = self._hold_reason(notification, now)
        if reason is not None:
            await self._repo.save(notification.model_copy(update={"suppressed_reason": reason}))
            return reason
        return await self._deliver(notification)

    async def _deliver(self, notification: Notification, *, hold_on_failure: bool = True) -> str:
        if self._sender is None:
            await self._repo.save(
                notification.model_copy(update={"suppressed_reason": "no_telegram"})
            )
            return "no_telegram"
        try:
            for chunk in split_message(notification.text):
                await self._sender.send(chunk)
                self._sent.append(self._clock.now())
        except Exception as exc:  # Telegram down / rejected: keep it for the next flush
            log.warning("notify.send_failed", kind=notification.kind, error=repr(exc)[:200])
            if hold_on_failure:
                await self._repo.save(
                    notification.model_copy(update={"suppressed_reason": "retry"})
                )
            return "retry"
        sent_at = self._clock.now()
        await self._repo.save(notification.model_copy(update={"sent_at": sent_at}))
        if notification.first_seen_at is not None and notification.ref_ids:
            await self._ops.record_latency(
                LatencyStage(
                    ref_id=notification.ref_ids[0],
                    source_id=notification.kind,
                    stage="notified",
                    ts=sent_at,
                    seconds_from_first_seen=(sent_at - notification.first_seen_at).total_seconds(),
                )
            )
        return "sent"

    # ------------------------------------------------------------------ held messages

    async def flush(self) -> int:
        """Retry failed 🔴 messages at once; deliver everything else held as one summary once
        quiet hours / mute are over. Returns how many held messages went out."""
        now = self._clock.now()
        held = await self._repo.held(now - HELD_LOOKBACK)
        delivered = 0
        for item in [n for n in held if n.priority == "high" and n.suppressed_reason == "retry"]:
            retry = item.model_copy(update={"suppressed_reason": None})
            if await self._deliver(retry) == "retry":
                return delivered  # Telegram still failing: try again next flush
            delivered += 1
            held.remove(item)
        if not held or self.muted(now) or self.quiet(now) or not self._rate_ok(now):
            return delivered
        urgent = any(n.suppressed_reason != "digest" for n in held)
        interval = timedelta(minutes=self._cfg.digest_interval_minutes)
        due = self._last_digest_at is None or now - self._last_digest_at >= interval
        if not (urgent or due):
            return delivered
        if len(held) == 1:  # nothing to merge: deliver it as it was written
            single = held[0].model_copy(update={"suppressed_reason": None})
            if await self._deliver(single) == "retry":
                return delivered
            self._last_digest_at = now
            return delivered + 1
        digest = self._digest(held, now)
        outcome = await self._deliver(digest, hold_on_failure=False)
        if outcome == "retry":  # items stay held; the next flush tries again
            return delivered
        await self._repo.mark([n.id for n in held], f"digested:{digest.id}")
        self._last_digest_at = now
        log.info("notify.digest", items=len(held), outcome=outcome)
        return delivered + len(held)

    @staticmethod
    def _digest(held: Sequence[Notification], now: datetime) -> Notification:
        return Notification(
            id=notification_id("digest", now.isoformat(), *(n.id for n in held)),
            kind="digest",
            priority="normal",
            text=render_digest([(n.created_at, n.text, n.priority == "high") for n in held]),
            created_at=now,
        )

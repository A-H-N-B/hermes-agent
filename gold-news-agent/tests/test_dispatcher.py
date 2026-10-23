"""Notification delivery rules: priority, quiet hours, mute, dedupe, rate limit + merge, digest,
retry after a Telegram failure, restart safety and notified latency."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.models import Notification
from gold_agent.models.common import Priority
from gold_agent.notify.dispatcher import Dispatcher, in_quiet_hours
from gold_agent.notify.format import TELEGRAM_LIMIT

DAY = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)  # 19:30 WIB
NIGHT = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)  # 01:00 WIB, inside quiet hours 00-05
DAWN = datetime(2026, 10, 13, 22, 0, 30, tzinfo=UTC)  # 05:00:30 WIB, quiet hours over


class FakeSender:
    def __init__(self, fail: int = 0) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, text: str) -> None:
        if self.fail:
            self.fail -= 1
            raise RuntimeError("telegram down")
        self.sent.append(text)


def note(
    n: int,
    priority: Priority = "normal",
    *,
    at: datetime = DAY,
    key: str | None = None,
    kind: str = "news",
    **extra: Any,
) -> Notification:
    return Notification.model_validate(
        {
            "id": f"n{n}",
            "kind": kind,
            "priority": priority,
            "text": f"pesan {n}",
            "created_at": at,
            "dedupe_key": key,
        }
        | extra
    )


@pytest.fixture
def make(ctx: AppContext, clock: ManualClock, settings: Settings) -> Callable[..., Any]:
    async def factory(sender: FakeSender | None = None, at: datetime = DAY) -> Dispatcher:
        clock.set(at)
        dispatcher = Dispatcher(
            settings, repo=ctx.notify, ops=ctx.ops, clock=clock, sender=sender or FakeSender()
        )
        await dispatcher.load()
        return dispatcher

    return factory


def test_quiet_window_wraps_midnight() -> None:
    assert in_quiet_hours(NIGHT, "00:00", "05:00")
    assert not in_quiet_hours(DAWN, "00:00", "05:00")
    late = datetime(2026, 10, 13, 16, 30, tzinfo=UTC)  # 23:30 WIB
    assert in_quiet_hours(late, "22:00", "05:00") and not in_quiet_hours(DAY, "22:00", "05:00")


async def test_high_priority_passes_quiet_hours_and_the_rest_waits_for_dawn(
    make: Callable[..., Any], clock: ManualClock
) -> None:
    sender = FakeSender()
    dispatcher = await make(sender, at=NIGHT)
    assert await dispatcher.submit(note(1, "high", at=NIGHT)) == "sent"
    assert await dispatcher.submit(note(2, at=NIGHT)) == "quiet"
    assert await dispatcher.submit(note(3, "digest", at=NIGHT)) == "quiet"
    assert await dispatcher.flush() == 0  # still quiet
    assert sender.sent == ["pesan 1"]
    clock.set(DAWN)
    assert await dispatcher.flush() == 2
    assert len(sender.sent) == 2
    digest = sender.sent[1]
    assert digest.startswith("🔵 <b>RINGKASAN</b> · 2 pembaruan (01:00–01:00 WIB)")
    assert "pesan 2" in digest and "pesan 3" in digest
    assert await dispatcher.flush() == 0  # nothing is delivered twice


async def test_same_key_is_sent_once(make: Callable[..., Any], clock: ManualClock) -> None:
    sender = FakeSender()
    dispatcher = await make(sender)
    assert await dispatcher.submit(note(1, "high", key="flash:us_cpi_mom", kind="flash")) == "sent"
    assert await dispatcher.submit(note(2, "high", key="flash:us_cpi_mom", kind="flash")) == (
        "duplicate"
    )
    clock.advance(31 * 60)  # flash keys expire after dedupe_minutes ...
    assert await dispatcher.submit(note(3, "high", key="flash:us_cpi_mom", kind="flash")) == "sent"
    assert await dispatcher.submit(note(4, key="news:c1")) == "sent"
    clock.advance(6 * 3600)  # ... a story is announced once, however late its update arrives
    assert await dispatcher.submit(note(5, key="news:c1")) == "duplicate"
    assert sender.sent == ["pesan 1", "pesan 3", "pesan 4"]


async def test_rate_limit_merges_the_overflow(
    make: Callable[..., Any], clock: ManualClock, settings: Settings
) -> None:
    sender = FakeSender()
    dispatcher = await make(sender)
    budget = settings.notifications.max_messages_per_minute
    outcomes = [await dispatcher.submit(note(n)) for n in range(budget + 2)]
    assert outcomes == ["sent"] * budget + ["rate", "rate"]
    assert await dispatcher.submit(note(99, "high")) == "sent"  # 🔴 is never throttled
    assert await dispatcher.flush() == 0  # the minute is not over
    clock.advance(61)
    assert await dispatcher.flush() == 2
    assert sender.sent[-1].startswith("🔵 <b>RINGKASAN</b> · 2 pembaruan")


async def test_single_held_message_goes_out_unchanged(
    make: Callable[..., Any], clock: ManualClock, settings: Settings
) -> None:
    sender = FakeSender()
    dispatcher = await make(sender)
    for n in range(settings.notifications.max_messages_per_minute):
        await dispatcher.submit(note(n))
    assert await dispatcher.submit(note(50)) == "rate"
    clock.advance(61)
    assert await dispatcher.flush() == 1
    assert sender.sent[-1] == "pesan 50"


async def test_mute_holds_normal_but_never_high(
    make: Callable[..., Any], clock: ManualClock
) -> None:
    sender = FakeSender()
    dispatcher = await make(sender)
    await dispatcher.mute(timedelta(hours=1))
    assert await dispatcher.submit(note(1)) == "muted"
    assert await dispatcher.submit(note(2, "high")) == "sent"
    assert await dispatcher.flush() == 0
    await dispatcher.mute(None)
    assert await dispatcher.flush() == 1
    assert sender.sent == ["pesan 2", "pesan 1"]


async def test_digest_items_wait_for_the_interval(
    make: Callable[..., Any], clock: ManualClock
) -> None:
    sender = FakeSender()
    dispatcher = await make(sender)
    assert await dispatcher.submit(note(1, "digest")) == "digest"
    assert await dispatcher.flush() == 1  # first flush: no digest sent yet in this run
    assert await dispatcher.submit(note(2, "digest")) == "digest"
    clock.advance(30 * 60)
    assert await dispatcher.flush() == 0  # interval is 60 minutes
    clock.advance(31 * 60)
    assert await dispatcher.flush() == 1


async def test_failed_send_is_retried_even_in_quiet_hours_for_high_priority(
    make: Callable[..., Any], clock: ManualClock
) -> None:
    sender = FakeSender(fail=1)
    dispatcher = await make(sender, at=NIGHT)
    assert await dispatcher.submit(note(1, "high", at=NIGHT)) == "retry"
    clock.advance(15)
    assert await dispatcher.flush() == 1
    assert sender.sent == ["pesan 1"]


async def test_held_messages_survive_a_restart_and_sent_ones_are_not_resent(
    make: Callable[..., Any], clock: ManualClock
) -> None:
    first = await make(FakeSender(), at=NIGHT)
    await first.submit(note(1, "high", at=NIGHT, key="signal:s1", kind="signal"))
    await first.submit(note(2, at=NIGHT))
    sender = FakeSender()
    restarted = await make(sender, at=DAWN)
    assert await restarted.submit(note(1, "high", at=DAWN, key="signal:s1", kind="signal")) == (
        "duplicate"
    )
    assert await restarted.flush() == 1
    assert sender.sent == ["pesan 2"]


async def test_long_messages_are_split_and_latency_is_recorded(
    make: Callable[..., Any], ctx: AppContext
) -> None:
    sender = FakeSender()
    dispatcher = await make(sender)
    body = "\n".join(f"baris {n} " + "y" * 80 for n in range(120))
    story = note(
        1,
        "high",
        text=body,
        ref_ids=["c9"],
        first_seen_at=DAY - timedelta(seconds=12),
    )
    assert await dispatcher.submit(story) == "sent"
    assert len(sender.sent) >= 3 and all(len(chunk) <= TELEGRAM_LIMIT for chunk in sender.sent)
    samples = await ctx.ops.latency_samples(DAY - timedelta(minutes=1))
    assert [(s.stage, s.ref_id, s.seconds_from_first_seen) for s in samples] == [
        ("notified", "c9", 12.0)
    ]


async def test_without_telegram_everything_is_recorded(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    clock.set(DAY)
    dispatcher = Dispatcher(settings, repo=ctx.notify, ops=ctx.ops, clock=clock, sender=None)
    assert await dispatcher.submit(note(1, "high")) == "no_telegram"
    recent = await ctx.notify.recent()
    assert [(n.id, n.suppressed_reason) for n in recent] == [("n1", "no_telegram")]

"""Watchdog: alerts on state changes (sources, services, queues, clock, disk), recoveries, and
the nightly retention clean-up."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_ALERTS, STREAM_NEWS_RAW
from gold_agent.core.clock import ManualClock
from gold_agent.models import Alert, MarketContext, SourceHealth, Tick
from gold_agent.ops.clock import file_offset, parse_tracking
from gold_agent.ops.watchdog import Watchdog
from gold_agent.services.watchdog import WatchdogService
from tests.conftest import make_source

NOW = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)


def watchdog(
    ctx: AppContext,
    tmp_path: Path,
    *,
    offset: float | None = 0.003,
    disk: tuple[int, int] = (50, 100),
) -> Watchdog:
    async def read_offset(_path: str | None) -> float | None:
        return offset

    return Watchdog(ctx, offset_reader=read_offset, disk_reader=lambda _p: disk, data_dir=tmp_path)


def titles(alerts: Sequence[Alert]) -> list[str]:
    return [a.title for a in alerts]


async def test_dead_source_alerts_once_then_recovers(
    ctx: AppContext, clock: ManualClock, tmp_path: Path
) -> None:
    clock.set(NOW)
    dog = watchdog(ctx, tmp_path)
    down = SourceHealth(
        source_id="feed",
        status="down",
        consecutive_errors=5,
        last_error="HTTP 503",
        last_success_at=NOW - timedelta(minutes=5),
        updated_at=NOW,
    )
    await ctx.ops.upsert_source_health(down)
    alerts = await dog.check()
    assert titles(alerts) == ["Sumber feed bermasalah"]
    assert alerts[0].severity == "critical"  # tier 1
    assert "5 error beruntun (HTTP 503)" in alerts[0].detail
    assert await dog.check() == []  # still down: no repeat
    await ctx.ops.upsert_source_health(
        down.model_copy(update={"status": "ok", "consecutive_errors": 0, "last_success_at": NOW})
    )
    recovered = await dog.check()
    assert titles(recovered) == ["Sumber feed normal kembali"]
    assert recovered[0].kind == "recovery"


async def test_stopped_polling_and_a_quiet_channel(
    ctx_factory: Callable[..., AppContext], clock: ManualClock, tmp_path: Path
) -> None:
    clock.set(NOW)
    squawk = make_source(id="squawk", silence_alert_minutes=20)
    context = ctx_factory([make_source(), squawk])
    await context.db.create_all()
    try:
        await context.ops.upsert_source_health(
            SourceHealth(
                source_id="feed",
                status="ok",
                last_success_at=NOW - timedelta(minutes=45),
                updated_at=NOW,
            )
        )
        await context.ops.upsert_source_health(
            SourceHealth(
                source_id="squawk",
                status="ok",
                last_success_at=NOW,
                last_item_at=NOW - timedelta(minutes=25),
                updated_at=NOW,
            )
        )
        alerts = {a.title: a.detail for a in await watchdog(context, tmp_path).check()}
        assert "tidak ada poll sukses sejak 18:45 WIB" in alerts["Sumber feed bermasalah"]
        assert alerts["Sumber squawk bermasalah"] == "tidak ada item baru selama 25 mnt"
    finally:
        await context.close()


async def test_service_heartbeat_queue_backlog_clock_and_disk(
    ctx: AppContext, clock: ManualClock, tmp_path: Path
) -> None:
    clock.set(NOW)
    await ctx.ops.heartbeat("signal", NOW - timedelta(minutes=5))
    await ctx.ops.heartbeat("market", NOW - timedelta(seconds=20))
    await ctx.bus.read(STREAM_NEWS_RAW, "analyzer", "a", block_ms=0)  # the group exists ...
    for n in range(501):  # ... and falls behind
        await ctx.bus.publish(STREAM_NEWS_RAW, {"n": n})
    dog = watchdog(ctx, tmp_path, offset=0.25, disk=(5, 100))
    alerts = {a.title: a for a in await dog.check()}
    assert alerts["Service signal tidak merespons"].severity == "critical"
    assert "Service market tidak merespons" not in alerts
    assert alerts["Antrian news.raw menumpuk"].detail.startswith("501 pesan belum diproses")
    assert alerts["Jam sistem melenceng"].detail == "offset chrony +250 ms (batas ±100)"
    assert alerts["Jam sistem melenceng"].kind == "clock"
    assert alerts["Disk hampir penuh"].detail == "sisa 5.0% (0.0 GB)"
    unknown = watchdog(ctx, tmp_path, offset=None)
    assert "Offset jam sistem tidak diketahui" in titles(await unknown.check())


def test_chrony_output_and_offset_file(tmp_path: Path) -> None:
    line = "A9FEA97B,169.254.169.123,4,1760358000.123,-0.000012345,0.000001,0.00002,-12.3,0,0"
    assert parse_tracking(line) == pytest.approx(-0.000012345)
    assert parse_tracking("garbage") is None
    offset = tmp_path / "clock_offset"
    offset.write_text("0.0042\n", encoding="utf-8")
    assert file_offset(offset) == pytest.approx(0.0042)
    assert file_offset(tmp_path / "missing") is None
    written = offset.stat().st_mtime - 20 * 60
    os.utime(offset, (written, written))  # the host timer stopped 20 minutes ago
    assert file_offset(offset) is None  # a stale offset is unknown, not "fine"


async def test_service_publishes_alerts_and_prunes_old_data_nightly(
    ctx: AppContext, clock: ManualClock, tmp_path: Path
) -> None:
    clock.set(NOW)
    old = NOW - timedelta(days=40)
    await ctx.market.insert_ticks(
        [
            Tick(ts=ts, bid=2600, ask=2600.2, source="mt5", received_at=ts)
            for ts in (old, NOW - timedelta(hours=1))
        ]
    )
    await ctx.market.save_context(MarketContext(ts=NOW - timedelta(days=200)))
    await ctx.market.save_context(MarketContext(ts=NOW))
    await ctx.ops.heartbeat("signal", NOW - timedelta(minutes=5))
    service = WatchdogService(ctx, watchdog(ctx, tmp_path))
    assert await service.check() == 1
    published = await ctx.bus.read(STREAM_ALERTS, "t", "t", block_ms=0)
    assert [m.data["title"] for m in published] == ["Service signal tidak merespons"]
    assert (await ctx.ops.kv_get("clock_offset")) == {"offset_seconds": 0.003}

    assert await service.maintenance()
    assert not await service.maintenance()  # once per night
    ticks = await ctx.market.ticks_between("mt5", old - timedelta(days=1), NOW)
    assert [t.ts for t in ticks] == [NOW - timedelta(hours=1)]
    contexts = await ctx.market.contexts_between(NOW - timedelta(days=365), NOW + timedelta(1))
    assert [c.ts for c in contexts] == [NOW]

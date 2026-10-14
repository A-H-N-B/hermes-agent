"""Watchdog (section 18): turns silent sources, dead services, growing queues, clock drift and a
filling disk into admin alerts, and says so again when each problem clears.

Price staleness is the market service's own alert (it knows the session calendar). Each check
has a key; an alert fires only on a change of state, never on every pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from gold_agent.core.app import DATA_DIR, AppContext
from gold_agent.core.bus import (
    STREAM_ANALYSES,
    STREAM_CONTEXT,
    STREAM_NEWS_RAW,
    STREAM_NOTIFY,
    STREAM_SIGNALS,
)
from gold_agent.models import Alert
from gold_agent.ops.clock import clock_offset
from gold_agent.timeutils.sessions import fmt_wib

# (stream, consumer group) pairs whose backlog means the pipeline is falling behind.
WATCHED_QUEUES: tuple[tuple[str, str], ...] = (
    (STREAM_NEWS_RAW, "analyzer"),
    (STREAM_ANALYSES, "signal"),
    (STREAM_SIGNALS, "notifier"),
    (STREAM_NOTIFY, "notifier-outbox"),
    (STREAM_CONTEXT, "signal-context"),
)

OffsetReader = Callable[[str | None], Awaitable[float | None]]
DiskReader = Callable[[Path], tuple[int, int]]  # (free, total) bytes


@dataclass(frozen=True)
class Finding:
    key: str
    problem: bool
    title: str
    detail: str
    critical: bool = False


def _alert_id(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def _disk_usage(path: Path) -> tuple[int, int]:
    usage = shutil.disk_usage(path)
    return usage.free, usage.total


class Watchdog:
    def __init__(
        self,
        ctx: AppContext,
        *,
        offset_reader: OffsetReader = clock_offset,
        disk_reader: DiskReader = _disk_usage,
        data_dir: Path = DATA_DIR,
    ) -> None:
        self._ctx = ctx
        self._offset_reader = offset_reader
        self._disk_reader = disk_reader
        self._data_dir = data_dir
        self._failing: dict[str, Finding] = {}
        self.clock_offset_seconds: float | None = None

    # ------------------------------------------------------------------ checks

    async def _services(self, now: datetime) -> list[Finding]:
        limit = timedelta(seconds=self._ctx.settings.watchdog.heartbeat_stale_seconds)
        findings: list[Finding] = []
        for name, beat in (await self._ctx.ops.heartbeats()).items():
            stale = now - beat > limit
            findings.append(
                Finding(
                    key=f"service:{name}",
                    problem=stale,
                    title=f"Service {name} tidak merespons" if stale else f"Service {name} pulih",
                    detail=f"heartbeat terakhir {fmt_wib(beat, with_date=True)} WIB",
                    critical=name in ("signal", "notifier", "market"),
                )
            )
        return findings

    async def _sources(self, now: datetime) -> list[Finding]:
        ctx = self._ctx
        default = timedelta(minutes=ctx.settings.watchdog.default_source_silence_minutes)
        registry = {s.id: s for s in ctx.sources.production()}
        findings: list[Finding] = []
        for health in await ctx.ops.source_health():
            source = registry.get(health.source_id)
            if source is None:
                continue
            problems: list[str] = []
            if health.status == "down":
                error = ctx.redact(health.last_error or "-")[:160]
                problems.append(f"{health.consecutive_errors} error beruntun ({error})")
            last_ok = health.last_success_at
            if last_ok is not None and now - last_ok > default:
                problems.append(f"tidak ada poll sukses sejak {fmt_wib(last_ok)} WIB")
            if source.silence_alert_minutes and health.last_item_at is not None:
                quiet = now - health.last_item_at
                if quiet > timedelta(minutes=source.silence_alert_minutes):
                    problems.append(
                        f"tidak ada item baru selama {quiet.total_seconds() / 60:.0f} mnt"
                    )
            findings.append(
                Finding(
                    key=f"source:{health.source_id}",
                    problem=bool(problems),
                    title=(
                        f"Sumber {health.source_id} bermasalah"
                        if problems
                        else f"Sumber {health.source_id} normal kembali"
                    ),
                    detail="; ".join(problems) or "poll dan item kembali normal",
                    critical=source.tier == 1,
                )
            )
        return findings

    async def _queues(self) -> list[Finding]:
        limit = self._ctx.settings.watchdog.queue_backlog_alert
        findings: list[Finding] = []
        for stream, group in WATCHED_QUEUES:
            lag = await self._ctx.bus.lag(stream, group)
            if lag is None:
                continue
            findings.append(
                Finding(
                    key=f"queue:{stream}:{group}",
                    problem=lag > limit,
                    title=f"Antrian {stream} menumpuk"
                    if lag > limit
                    else f"Antrian {stream} lancar",
                    detail=f"{lag} pesan belum diproses oleh {group} (batas {limit})",
                )
            )
        return findings

    async def _clock(self) -> list[Finding]:
        cfg = self._ctx.settings.watchdog
        offset = await self._offset_reader(cfg.clock_offset_file)
        self.clock_offset_seconds = offset
        if offset is None:
            return [
                Finding(
                    key="clock:unknown",
                    problem=True,
                    title="Offset jam sistem tidak diketahui",
                    detail="chrony tidak terdeteksi: pasang chrony agar latensi bisa dipercaya",
                )
            ]
        limit = cfg.clock_offset_alert_ms
        too_far = abs(offset) * 1000 > limit
        return [
            Finding(key="clock:unknown", problem=False, title="", detail=""),
            Finding(
                key="clock:offset",
                problem=too_far,
                title="Jam sistem melenceng" if too_far else "Jam sistem sinkron kembali",
                detail=f"offset chrony {offset * 1000:+.0f} ms (batas ±{limit:.0f})",
                critical=abs(offset) > 1.0,
            ),
        ]

    async def _disk(self) -> list[Finding]:
        path = self._data_dir if self._data_dir.exists() else Path("/")
        free, total = await asyncio.to_thread(self._disk_reader, path)
        pct = 100 * free / total if total else 100.0
        low = pct < self._ctx.settings.watchdog.disk_free_min_pct
        return [
            Finding(
                key="disk",
                problem=low,
                title="Disk hampir penuh" if low else "Ruang disk cukup kembali",
                detail=f"sisa {pct:.1f}% ({free / 1e9:.1f} GB)",
                critical=pct < 3,
            )
        ]

    # ------------------------------------------------------------------ transitions

    def _transitions(self, findings: Sequence[Finding], now: datetime) -> list[Alert]:
        alerts: list[Alert] = []
        for finding in findings:
            was = self._failing.get(finding.key)
            if finding.problem and was is None:
                self._failing[finding.key] = finding
                alerts.append(
                    Alert(
                        id=_alert_id(finding.key, now),
                        kind="clock" if finding.key.startswith("clock") else "health",
                        severity="critical" if finding.critical else "warning",
                        title=finding.title,
                        detail=finding.detail,
                        created_at=now,
                        dedupe_key=f"watchdog:{finding.key}",
                    )
                )
            elif not finding.problem and was is not None:
                del self._failing[finding.key]
                if finding.title:
                    alerts.append(
                        Alert(
                            id=_alert_id(finding.key, "ok", now),
                            kind="recovery",
                            severity="info",
                            title=finding.title,
                            detail=finding.detail,
                            created_at=now,
                            dedupe_key=f"watchdog-ok:{finding.key}",
                        )
                    )
        return alerts

    async def check(self) -> list[Alert]:
        now = self._ctx.clock.now()
        findings = [
            *await self._services(now),
            *await self._sources(now),
            *await self._queues(),
            *await self._clock(),
            *await self._disk(),
        ]
        return self._transitions(findings, now)

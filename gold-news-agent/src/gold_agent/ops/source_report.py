"""Render source verification results as a Markdown report (Bahasa Indonesia)."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from gold_agent.ops.source_check import LagStats, SourceCheck

STATUS_LABEL = {"ok": "✅ OK", "warn": "⚠️ Cek", "fail": "❌ Gagal", "skipped": "⏭️ Dilewati"}
PRECISION_LABEL = {"second": "detik", "minute": "menit", "date": "tanggal", "none": "—"}


class RunMeta(BaseModel):
    started_at: datetime
    finished_at: datetime
    display_tz: str
    python: str
    platform: str
    user_agent: str
    watch_minutes: float
    environment_note: str | None = None


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    sign = "-" if seconds < 0 else ""
    value = abs(seconds)
    if value < 120:
        return f"{sign}{value:.1f} dtk".replace(".", ",")
    if value < 7200:
        return f"{sign}{value / 60:.1f} mnt".replace(".", ",")
    if value < 172800:
        return f"{sign}{value / 3600:.1f} jam".replace(".", ",")
    return f"{sign}{value / 86400:.1f} hari".replace(".", ",")


def fmt_time(moment: datetime | None, zone: ZoneInfo) -> str:
    if moment is None:
        return "—"
    local = moment.astimezone(zone)
    return f"{local:%Y-%m-%d %H:%M:%S} {local.tzname()}"


def _cache_cell(check: SourceCheck) -> str:
    if check.has_etag is None:
        return "—"
    flags = [name for name, on in (("ETag", check.has_etag), ("LM", check.has_last_modified)) if on]
    if not flags:
        return "tidak ada"
    tail = {True: " → 304 ✓", False: " → 304 ✗", None: ""}[check.conditional_304]
    return "/".join(flags) + tail


def _lag_row(source_id: str, lag: LagStats) -> str:
    return (
        f"| {source_id} | {lag.samples} | {fmt_duration(lag.p50_seconds)} | "
        f"{fmt_duration(lag.p95_seconds)} | {fmt_duration(lag.max_seconds)} | {lag.negative} |"
    )


def _clock_line(results: Sequence[SourceCheck]) -> str:
    offsets = [
        r.server_clock_offset_seconds for r in results if r.server_clock_offset_seconds is not None
    ]
    if not offsets:
        return "tidak ada header `Date` yang terbaca, jadi selisih jam tidak bisa diperkirakan"
    median = f"{statistics.median(offsets):+.1f}".replace(".", ",")
    return (
        f"median {median} dtk dari {len(offsets)} server "
        "(resolusi header `Date` 1 dtk; hanya cek kasar, bukan pengganti NTP/chrony)"
    )


def render_markdown(results: Sequence[SourceCheck], meta: RunMeta) -> str:
    zone = ZoneInfo(meta.display_tz)
    counts = Counter(r.status for r in results)
    lines = [
        "# Laporan Verifikasi Sumber",
        "",
        "> Dibuat otomatis oleh `scripts/verify_sources.py`. Jangan diedit manual: jalankan ulang "
        "skripnya. Semua angka berasal dari request nyata pada waktu di bawah.",
        "",
        "## Lingkungan pengukuran",
        "",
        f"- Waktu: {fmt_time(meta.started_at, zone)} s/d {fmt_time(meta.finished_at, zone)}",
        f"- Host: {meta.platform} · Python {meta.python}",
        f"- User-Agent: `{meta.user_agent}`",
        f"- Selisih jam lokal vs server: {_clock_line(results)}",
    ]
    if meta.watch_minutes > 0:
        lines.append(
            f"- Mode watch: {meta.watch_minutes:g} menit. Lag = `first_seen − published` "
            "untuk item yang muncul selama jendela, termasuk ≤ 1 interval polling."
        )
    else:
        lines.append(
            "- Mode watch: **tidak dijalankan**, jadi latensi sumber belum terukur. "
            "Jalankan dengan `--watch-minutes 60` atau lebih di host produksi."
        )
    if meta.environment_note:
        lines.append(f"- **Catatan lingkungan:** {meta.environment_note}")

    lines += [
        "",
        "## Ringkasan",
        "",
        f"Total {len(results)} sumber: "
        + ", ".join(f"{STATUS_LABEL[s]} {counts.get(s, 0)}" for s in STATUS_LABEL),
        "",
        "## Hasil per sumber",
        "",
        "| Status | ID | Tier | Jenis | HTTP | Respons | Item | Item terbaru (umur) "
        "| Presisi waktu | Cache | robots.txt | Alasan |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda c: (c.tier, c.kind, c.source_id)):
        newest = (
            f"{fmt_time(r.newest_published, zone)} ({fmt_duration(r.newest_age_seconds)})"
            if r.newest_published
            else "—"
        )
        precision = PRECISION_LABEL[r.timestamp_precision] if r.timestamp_precision else "—"
        elapsed = fmt_duration(r.elapsed_ms / 1000) if r.elapsed_ms is not None else "—"
        lines.append(
            f"| {STATUS_LABEL[r.status]} | `{r.source_id}` | {r.tier} | {r.kind}/{r.type} "
            f"| {r.http_status or '—'} | {elapsed} "
            f"| {r.item_count if r.item_count is not None else '—'} | {newest} | {precision} "
            f"| {_cache_cell(r)} | {r.robots} | {_cell(r.reason)} |"
        )

    watched = [r for r in results if r.watch_lag is not None]
    if watched:
        lines += [
            "",
            "## Latensi sumber (mode watch)",
            "",
            "| ID | Sampel | p50 | p95 | Maks | Negatif* |",
            "|---|---|---|---|---|---|",
        ]
        lines += [_lag_row(r.source_id, r.watch_lag) for r in watched if r.watch_lag]
        lines.append("")
        lines.append(
            "\\* Negatif = item tercatat terbit *setelah* kita melihatnya: jam/zona waktu sumber "
            "salah atau dibulatkan. Sumber seperti ini tidak boleh dipakai untuk mengukur latensi."
        )

    sockets = [r for r in results if r.ws_lag is not None]
    if sockets:
        lines += [
            "",
            "## WebSocket (lag = waktu terima − event time dari bursa)",
            "",
            "| ID | Sampel | p50 | p95 | Maks | Negatif |",
            "|---|---|---|---|---|---|",
        ]
        lines += [_lag_row(r.source_id, r.ws_lag) for r in sockets if r.ws_lag]

    lines += ["", "## Catatan per sumber & ToS", ""]
    for r in sorted(results, key=lambda c: c.source_id):
        detail = [f"- `{r.source_id}` ({_cell(r.name)}): {_cell(r.tos_note)}"]
        if r.tos_url:
            detail.append(f" ToS: {r.tos_url}.")
        if r.final_url and r.url and r.final_url != r.url:
            detail.append(f" Redirect ke `{r.final_url}`.")
        if r.notes:
            detail.append(" " + "; ".join(_cell(n) for n in r.notes) + ".")
        lines.append("".join(detail))

    lines += [
        "",
        "## Langkah berikutnya",
        "",
        "1. Sumber ✅ OK: baca ToS-nya, lalu ubah `status: candidate` menjadi "
        "`status: verified` di `config/sources.yaml`. Collector hanya menjalankan sumber "
        "`verified`.",
        "2. Sumber ⚠️/❌: perbaiki URL (lihat redirect/catatan), isi key di `.env`, atau ubah "
        "menjadi `status: rejected`.",
        "3. Ukur latensi di host produksi: "
        "`uv run python scripts/verify_sources.py --watch-minutes 120`, idealnya mencakup satu "
        "rilis data AS (19:30/20:30 WIB).",
        "",
    ]
    return "\n".join(lines)

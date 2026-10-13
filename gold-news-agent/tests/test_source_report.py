from __future__ import annotations

from datetime import UTC, datetime

from gold_agent.config import SourceKind, SourceType
from gold_agent.ops.source_check import LagStats, SourceCheck
from gold_agent.ops.source_report import RunMeta, fmt_duration, render_markdown

T0 = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)


def _check(**overrides: object) -> SourceCheck:
    base: dict[str, object] = {
        "source_id": "fed_press_all",
        "name": "Fed | press",
        "kind": SourceKind.NEWS,
        "type": SourceType.RSS,
        "tier": 2,
        "status": "ok",
        "reason": "OK",
        "checked_at": T0,
        "tos_note": "public",
    }
    return SourceCheck.model_validate(base | overrides)


def _meta(watch_minutes: float = 0.0) -> RunMeta:
    return RunMeta(
        started_at=T0,
        finished_at=T0,
        display_tz="Asia/Jakarta",
        python="3.12",
        platform="Linux",
        user_agent="ua",
        watch_minutes=watch_minutes,
    )


def test_durations_use_indonesian_units_and_decimal_comma() -> None:
    assert fmt_duration(None) == "—"
    assert fmt_duration(4.25) == "4,2 dtk"
    assert fmt_duration(300) == "5,0 mnt"
    assert fmt_duration(-7200) == "-2,0 jam"


def test_report_shows_times_in_wib_and_says_when_latency_was_not_measured() -> None:
    report = render_markdown([_check(newest_published=T0, newest_age_seconds=60)], _meta())
    assert "19:30:00 WIB" in report
    assert "tidak dijalankan" in report
    assert "Latensi sumber (mode watch)" not in report


def test_report_includes_watch_latency_when_measured() -> None:
    lag = LagStats(samples=4, p50_seconds=12.0, p95_seconds=40.0, max_seconds=55.0, negative=0)
    report = render_markdown([_check(watch_lag=lag)], _meta(watch_minutes=60))
    assert "Latensi sumber (mode watch)" in report
    assert "| fed_press_all | 4 | 12,0 dtk | 40,0 dtk | 55,0 dtk | 0 |" in report


def test_table_cells_escape_pipes() -> None:
    report = render_markdown([_check(reason="HTTP 403 | blocked")], _meta())
    assert "HTTP 403 \\| blocked" in report

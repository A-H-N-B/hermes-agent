"""Evaluator service: scheduled nightly/weekly jobs, reports on disk and Telegram, learning outputs,
no re-runs after a restart, honest output with an empty archive."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_NOTIFY
from gold_agent.core.clock import ManualClock
from gold_agent.evaluation.reports import ReportNarrative, report_period
from gold_agent.models import Candle, SignalOutcome
from gold_agent.services.evaluator import EvaluatorService
from gold_agent.signals.engine import KV_EVENT_MOVES
from tests.builders import analysis
from tests.fakes import FakeAnthropic, parsed, timeout_error
from tests.test_paper import buy

# Wednesday 14 Oct 2026, 05:31 WIB: the nightly slot (05:30) has just passed.
NIGHTLY = datetime(2026, 10, 13, 22, 31, tzinfo=UTC)


async def seed(ctx: AppContext, settings: object) -> None:
    day_start = datetime(2026, 10, 13, 0, 0, tzinfo=UTC) - timedelta(hours=7)  # 13 Oct 00:00 WIB
    bars = [
        Candle(
            source="mt5",
            tf="M1",
            open_time=day_start + timedelta(minutes=m),
            close_time=day_start + timedelta(minutes=m + 1),
            open=2600 + m * 0.01,
            high=2600 + m * 0.01 + 0.1,
            low=2600 + m * 0.01 - 0.1,
            close=2600 + (m + 1) * 0.01,
        )
        for m in range(24 * 60)
    ]
    await ctx.market.upsert_candles(bars)
    for i in range(3):
        await ctx.news.save_analysis(
            analysis(f"c{i}", first_seen=day_start + timedelta(hours=8 + i), impact_score=4)
        )
    signal = buy(settings)  # type: ignore[arg-type]
    await ctx.signals.save_signal(signal)
    await ctx.signals.save_outcome(
        SignalOutcome(
            signal_id=signal.signal_id,
            status="tp2",
            entry_price=2650.15,
            opened_at=signal.created_at_utc,
            closed_at=signal.created_at_utc + timedelta(minutes=30),
            r_multiple=1.48,
        )
    )


async def test_nightly_and_weekly_jobs_run_once_and_write_reports(
    ctx: AppContext, clock: ManualClock, settings: object, tmp_path: Path
) -> None:
    clock.set(NIGHTLY)
    await seed(ctx, settings)
    narrative = ReportNarrative(narrative_id="Emas menguat setelah serangan rudal.")
    evaluator = EvaluatorService(
        ctx,
        anthropic_client=FakeAnthropic(parsed(narrative), timeout_error()),
        reports_dir=tmp_path,
    )
    assert await evaluator.tick() == ["nightly", "weekly"]  # first start catches up both
    assert await evaluator.tick() == []
    restarted = EvaluatorService(
        ctx, anthropic_client=FakeAnthropic(parsed(narrative)), reports_dir=tmp_path
    )
    assert await restarted.tick() == []  # the ledger survives restarts

    daily = (tmp_path / "harian-2026-10-13.md").read_text(encoding="utf-8")
    assert daily.startswith("# Laporan harian · Selasa 13 Okt")
    assert "Emas menguat setelah serangan rudal." in daily
    assert "- Sinyal: 1 (BUY 1, SELL 0)" in daily
    assert "sampel belum cukup (1 sinyal tertutup, butuh ≥ 30)" in daily
    weekly_name = f"mingguan-{report_period('mingguan', NIGHTLY)[0]:%Y-%m-%d}.md"
    assert (tmp_path / weekly_name).exists()
    study = (tmp_path / "event_study.md").read_text(encoding="utf-8")
    assert "3 berita relevan dengan harga" in study
    assert await ctx.ops.kv_get(KV_EVENT_MOVES) == {}  # too few releases to publish

    messages = await ctx.bus.read(STREAM_NOTIFY, "t", "t", count=10, block_ms=0)
    kinds = [m.data["kind"] for m in messages]
    assert kinds == ["daily_report", "weekly_report"]  # no calibration proposal: too few signals
    assert messages[0].data["text"].startswith("🧾 <b>Laporan harian · Selasa 13 Okt</b>")

    clock.set(NIGHTLY + timedelta(days=1))
    assert await restarted.tick() == ["nightly"]


async def test_a_failing_job_backs_off_instead_of_retrying_every_tick(
    ctx: AppContext, clock: ManualClock, tmp_path: Path
) -> None:
    clock.set(NIGHTLY)
    evaluator = EvaluatorService(ctx, anthropic_client=FakeAnthropic(), reports_dir=tmp_path)
    attempts: list[datetime] = []

    async def broken(now: datetime) -> None:
        attempts.append(now)
        raise RuntimeError("disk full")

    evaluator.jobs = [(evaluator.jobs[0][0], broken)]
    assert await evaluator.tick() == []
    clock.advance(60)
    assert await evaluator.tick() == []
    assert len(attempts) == 1
    clock.advance(15 * 60)
    await evaluator.tick()
    assert len(attempts) == 2


async def test_empty_archive_still_produces_an_honest_report(
    ctx: AppContext, clock: ManualClock, tmp_path: Path
) -> None:
    clock.set(NIGHTLY)
    evaluator = EvaluatorService(ctx, anthropic_client=FakeAnthropic(), reports_dir=tmp_path)
    data = await evaluator.report("harian", NIGHTLY, use_llm=False)
    assert data.signals == [] and data.performance.n_closed == 0
    text = (tmp_path / "harian-2026-10-13.md").read_text(encoding="utf-8")
    assert "- Tidak ada berita berdampak ≥ 3." in text
    assert "sampel belum cukup (0 sinyal tertutup, butuh ≥ 30)" in text
    proposal = await evaluator.calibrate(NIGHTLY)
    assert not proposal.ready and proposal.reason.startswith("sampel belum cukup")

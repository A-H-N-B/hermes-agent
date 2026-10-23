"""Evaluator service: the nightly learning pass (event study -> typical event moves for the
outlook, learned source reliability) plus the daily report, and weekly the weekly report and a
weight-calibration check that is only ever a proposal for a human."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from gold_agent.analysis.llm_client import LlmClient, LlmFailed, LlmUnavailable
from gold_agent.analysis.prompts import load_prompt
from gold_agent.core.app import DATA_DIR, AppContext
from gold_agent.core.bus import STREAM_NOTIFY
from gold_agent.core.schedule import Job, JobLedger
from gold_agent.evaluation import event_study
from gold_agent.evaluation.calibration import WeightProposal, propose_weights
from gold_agent.evaluation.loaders import load_prices, load_state_samples
from gold_agent.evaluation.reliability import learn_reliability
from gold_agent.evaluation.reports import (
    ReportData,
    ReportKind,
    ReportNarrative,
    collect,
    narrative_prompt,
    render_markdown,
    render_telegram,
    report_period,
)
from gold_agent.models import Notification
from gold_agent.notify.format import esc
from gold_agent.notify.policy import notification_id
from gold_agent.signals.engine import KV_EVENT_MOVES

log = structlog.get_logger(__name__)

TICK_SECONDS = 60.0
RETRY_AFTER = timedelta(minutes=15)


class EvaluatorService:
    name = "evaluator"

    def __init__(
        self,
        ctx: AppContext,
        *,
        anthropic_client: Any | None = None,
        reports_dir: Path = DATA_DIR / "reports",
    ) -> None:
        self._ctx = ctx
        cfg = ctx.settings.reports
        self.reports_dir = reports_dir
        self.ledger = JobLedger(ctx.ops)
        self.llm = LlmClient(
            ctx.settings,
            api_key=ctx.env.get("ANTHROPIC_API_KEY"),
            ops=ctx.ops,
            clock=ctx.clock,
            client=anthropic_client,
        )
        self._retry_at: dict[str, datetime] = {}
        self.jobs: list[tuple[Job, Callable[[datetime], Awaitable[None]]]] = [
            (Job("nightly", cfg.daily.zone, cfg.daily.local), self.nightly),
            (Job("weekly", cfg.weekly.zone, cfg.weekly.local, cfg.weekly.weekday), self.weekly),
        ]

    # ------------------------------------------------------------------ jobs

    async def nightly(self, now: datetime) -> None:
        await self.learn(now)
        await self.report("harian", now)

    async def weekly(self, now: datetime) -> None:
        await self.report("mingguan", now)
        proposal = await self.calibrate(now)
        if proposal.ready and proposal.proposed is not None:
            text = "\n".join(
                [
                    "🧪 <b>Usulan bobot skor baru</b> (butuh persetujuanmu)",
                    esc(proposal.reason),
                    f"IC holdout: sekarang {proposal.holdout_ic_current} → usulan "
                    f"{proposal.holdout_ic_proposed}",
                    *(f"<code>{esc(line)}</code>" for line in proposal.yaml_snippet().split("\n")),
                    "Terapkan dengan mengubah config/settings.yaml lalu restart service signal.",
                ]
            )
            await self._notify("brief", text, f"calibration:{now:%Y-%m-%d}", now)

    async def learn(self, now: datetime) -> list[event_study.EventMove]:
        ctx = self._ctx
        cfg = ctx.settings.reports
        start = now - timedelta(days=cfg.event_study_days)
        analyses = await ctx.news.analyses_between(start, now - timedelta(minutes=61))
        prices = await load_prices(ctx, start, now)
        moves = event_study.measure(analyses, prices)
        typical = event_study.typical_event_moves(moves, min_samples=cfg.min_event_samples)
        await ctx.ops.kv_set(KV_EVENT_MOVES, typical, now)
        priors = {source.id: source.reliability for source in ctx.sources.sources}
        learned = learn_reliability(
            moves,
            priors,
            prior_weight=cfg.reliability_prior_weight,
            min_samples=cfg.reliability_min_samples,
        )
        for source_id, (value, samples) in learned.items():
            await ctx.ops.set_learned_reliability(source_id, value, samples, now)
        await self.write_report(
            "event_study.md", event_study.render_markdown(moves, start=start, end=now)
        )
        log.info("evaluator.learned", stories=len(moves), events=len(typical), sources=len(learned))
        return moves

    async def report(
        self, kind: ReportKind, now: datetime, *, use_llm: bool | None = None
    ) -> ReportData:
        start, end = report_period(kind, now)
        data = await collect(self._ctx, kind, start, end)
        wants_llm = self._ctx.settings.reports.use_llm if use_llm is None else use_llm
        if wants_llm and self.llm.available:
            try:
                result = await self.llm.structured(
                    "brief",
                    system=load_prompt("brief").text,
                    user=narrative_prompt(data),
                    schema=ReportNarrative,
                    ref_id=f"report:{kind}:{start:%Y%m%d}",
                )
                data.narrative = result.value.narrative_id
            except (LlmUnavailable, LlmFailed) as exc:
                data.notes.append(f"ringkasan AI tidak tersedia ({exc})")
        await self.write_report(f"{kind}-{start:%Y-%m-%d}.md", render_markdown(data))
        await self._notify(
            "daily_report" if kind == "harian" else "weekly_report",
            render_telegram(data),
            f"report:{kind}:{start:%Y-%m-%d}",
            now,
        )
        return data

    async def calibrate(self, now: datetime) -> WeightProposal:
        ctx = self._ctx
        since = now - timedelta(days=180)
        outcomes = await ctx.signals.outcomes_between(since, now)
        closed = sum(1 for o in outcomes if o.closed_at is not None and o.r_multiple is not None)
        samples = await load_state_samples(ctx, since, now - timedelta(minutes=61))
        proposal = propose_weights(
            samples, ctx.settings.signal_engine.weights, closed_signals=closed
        )
        log.info("evaluator.calibration", ready=proposal.ready, reason=proposal.reason)
        return proposal

    # ------------------------------------------------------------------ plumbing

    async def write_report(self, name: str, text: str) -> Path:
        def write() -> Path:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            path = self.reports_dir / name
            path.write_text(text, encoding="utf-8")
            return path

        return await asyncio.to_thread(write)

    async def _notify(self, kind: str, text: str, key: str, now: datetime) -> None:
        notification = Notification.model_validate(
            {
                "id": notification_id(key, now.isoformat()),
                "kind": kind,
                "priority": "normal",
                "text": text,
                "created_at": now,
                "dedupe_key": key,
            }
        )
        await self._ctx.bus.publish(STREAM_NOTIFY, notification)

    async def tick(self) -> list[str]:
        """Run every job whose slot has passed since its last run; returns their names.
        A failed job is retried after RETRY_AFTER, not every tick."""
        ran: list[str] = []
        for job, action in self.jobs:
            now = self._ctx.clock.now()
            if now < self._retry_at.get(job.name, now) or not job.due(
                now, await self.ledger.last_run(job)
            ):
                continue
            try:
                await action(now)
            except Exception:
                log.exception("evaluator.job_failed", job=job.name)
                self._retry_at[job.name] = now + RETRY_AFTER
                continue
            await self.ledger.mark(job, now)
            ran.append(job.name)
        return ran

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.tick()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)

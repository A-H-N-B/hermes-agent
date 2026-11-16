"""Evaluation commands: reports, event study, walk-forward backtest, weight calibration."""

from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta
from typing import Any, Protocol

from gold_agent.core.app import build_context
from gold_agent.evaluation.backtest import render_markdown, walk_forward
from gold_agent.evaluation.loaders import load_replay
from gold_agent.services.evaluator import EvaluatorService
from gold_agent.timeutils.sessions import WIB, SessionClock


def _as_of(text: str | None) -> datetime | None:
    """A WIB date -> the moment just after that day, so its report covers the day itself."""
    if text is None:
        return None
    day = datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007 - a calendar date only
    return datetime.combine(day + timedelta(days=1), time(0, 30), tzinfo=WIB)


async def report(args: argparse.Namespace) -> int:
    ctx = build_context(bus_kind="memory")
    try:
        evaluator = EvaluatorService(ctx)
        now = _as_of(args.date) or ctx.clock.now()
        data = await evaluator.report(args.kind, now, use_llm=args.llm)
        perf = data.performance
        print(f"laporan {args.kind}: {len(data.signals)} sinyal, {perf.n_closed} tertutup")
        for note in data.running.notes:
            print(f"  - {note}")
        print(f"file: {evaluator.reports_dir}")
        return 0
    finally:
        await ctx.close()


async def event_study(args: argparse.Namespace) -> int:
    ctx = build_context(bus_kind="memory")
    try:
        settings = ctx.settings.reports.model_copy(update={"event_study_days": args.days})
        ctx.settings = ctx.settings.model_copy(update={"reports": settings})
        evaluator = EvaluatorService(ctx)
        moves = await evaluator.learn(ctx.clock.now())
        print(f"{len(moves)} berita relevan dengan harga; laporan: {evaluator.reports_dir}")
        return 0
    finally:
        await ctx.close()


async def backtest(args: argparse.Namespace) -> int:
    ctx = build_context(bus_kind="memory")
    try:
        end = ctx.clock.now()
        start = end - timedelta(days=args.days)
        replay = await load_replay(ctx, start, end)
        if not replay.contexts:
            print("Belum ada snapshot pasar tersimpan untuk periode ini: jalankan service market.")
            return 2
        folds, combined = walk_forward(
            replay, ctx.settings, SessionClock(ctx.settings.sessions), folds=args.folds
        )
        text = render_markdown(folds, combined, start=start, end=end)
        path = await EvaluatorService(ctx).write_report("backtest.md", text)
        print(text)
        print(f"\nfile: {path}")
        return 0
    finally:
        await ctx.close()


async def calibrate(_args: argparse.Namespace) -> int:
    ctx = build_context(bus_kind="memory")
    try:
        proposal = await EvaluatorService(ctx).calibrate(ctx.clock.now())
        print(proposal.reason)
        if proposal.ic_train:
            print(f"IC data latih: {proposal.ic_train}")
        if proposal.proposed is not None:
            print(
                f"IC holdout: sekarang {proposal.holdout_ic_current}, usulan "
                f"{proposal.holdout_ic_proposed}"
            )
            print(proposal.yaml_snippet())
        return 0 if proposal.ready else 1
    finally:
        await ctx.close()


class SubParsers(Protocol):
    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser: ...


def add_parsers(sub: SubParsers) -> None:
    rep = sub.add_parser("report", help="daily/weekly report (Bahasa Indonesia)")
    rep.add_argument("kind", choices=["harian", "mingguan"])
    rep.add_argument("--date", help="WIB date (YYYY-MM-DD) whose report to build")
    rep.add_argument("--llm", action="store_true", help="add the AI narrative (costs money)")
    study = sub.add_parser("event-study", help="price moves after news (+1/+5/+15/+60 min)")
    study.add_argument("--days", type=int, default=90)
    test = sub.add_parser("backtest", help="walk-forward replay of stored snapshots")
    test.add_argument("--days", type=float, default=30)
    test.add_argument("--folds", type=int, default=4)
    sub.add_parser("calibrate", help="propose composite weights (needs >= 100 closed signals)")


HANDLERS = {
    "report": report,
    "event-study": event_study,
    "backtest": backtest,
    "calibrate": calibrate,
}

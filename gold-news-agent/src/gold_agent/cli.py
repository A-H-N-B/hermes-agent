"""`gold-agent` command line."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import structlog

from gold_agent.config import PROJECT_ROOT
from gold_agent.core.app import DATA_DIR, AppContext, build_context
from gold_agent.core.logging import configure_logging
from gold_agent.core.service import install_signal_handlers, supervise
from gold_agent.services.registry import SERVICE_FACTORIES
from gold_agent.storage.migrate import upgrade

log = structlog.get_logger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gold-agent", description="Gold News Agent (XAUUSD)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one or more services (or 'all')")
    run.add_argument("services", nargs="+", choices=[*SERVICE_FACTORIES, "all"])
    run.add_argument("--bus", choices=["memory", "redis"], help="override settings.infra.bus")
    run.add_argument("--no-migrate", action="store_true", help="skip `db upgrade` at start")
    run.add_argument(
        "--dashboard-host",
        help="override dashboard.host (Docker: 0.0.0.0, published on the host's 127.0.0.1; "
        "needs DASHBOARD_PASSWORD)",
    )

    db = sub.add_parser("db", help="database maintenance")
    db.add_argument("action", choices=["upgrade"])

    sub.add_parser("telegram-login", help="one-time Telethon login for the channel reader")

    health = sub.add_parser("healthcheck", help="exit 0 if the service heartbeat is fresh")
    health.add_argument("service")
    health.add_argument("--max-age", type=float, default=180.0)

    labels = sub.add_parser("labels", help="labelled headline set for the triage evaluation")
    label_sub = labels.add_subparsers(dest="labels_command", required=True)
    export = label_sub.add_parser("export", help="write archived headlines to label (JSONL)")
    export.add_argument("--days", type=float, default=14)
    export.add_argument("--limit", type=int, default=150)
    export.add_argument("--all", action="store_true", help="include prefilter-irrelevant items")
    export.add_argument("--out", type=Path, default=DATA_DIR / "labels" / "todo.jsonl")
    imp = label_sub.add_parser("import", help="store a filled JSONL file")
    imp.add_argument("path", type=Path)
    imp.add_argument("--labeler", required=True)

    evaluate = sub.add_parser("eval-triage", help="score prefilter/rules/LLM on the labels")
    evaluate.add_argument("--labeler", help="only this labeller's labels")
    evaluate.add_argument("--llm", action="store_true", help="also call Claude (costs money)")
    evaluate.add_argument("--out", type=Path, default=PROJECT_ROOT / "docs" / "eval_triage.md")

    from gold_agent.cli_eval import add_parsers
    from gold_agent.cli_ops import add_parsers as add_ops_parsers

    add_parsers(sub)
    add_ops_parsers(sub)
    sub.add_parser("verify-sources", help="Phase 0 source verification", add_help=False)
    return parser


async def _prepare(ctx: AppContext, *, migrate: bool) -> None:
    configure_logging(
        ctx.settings.logging.level, ctx.settings.logging.format, secrets=ctx.env.values()
    )
    if migrate:
        await upgrade(ctx.db.url)
    await ctx.ops.sync_sources(ctx.sources, ctx.clock.now())


async def _run(
    names: Sequence[str], bus: str | None, migrate: bool, dashboard_host: str | None
) -> int:
    ctx = build_context(bus_kind=bus)
    if dashboard_host:
        dashboard = ctx.settings.dashboard.model_copy(update={"host": dashboard_host})
        ctx.settings = ctx.settings.model_copy(update={"dashboard": dashboard})
    await _prepare(ctx, migrate=migrate)
    selected = list(SERVICE_FACTORIES) if "all" in names else list(dict.fromkeys(names))
    if (
        ctx.settings.infra.bus == "memory"
        and bus != "redis"
        and len(selected) < len(SERVICE_FACTORIES)
    ):
        log.warning(
            "run.memory_bus_partial", hint="memory bus only connects services in this process"
        )
    services = [SERVICE_FACTORIES[name](ctx) for name in selected]
    stop = asyncio.Event()
    install_signal_handlers(stop)
    log.info("run.start", services=selected, mode=ctx.settings.mode)

    async def heartbeat(name: str) -> None:
        await ctx.ops.heartbeat(name, ctx.clock.now())

    try:
        await supervise(services, stop, heartbeat=heartbeat)
    finally:
        await ctx.close()
    return 0


async def _db(action: str) -> int:
    ctx = build_context()
    if action == "upgrade":
        await upgrade(ctx.db.url)
        print("database upgraded")
    await ctx.close()
    return 0


async def _healthcheck(service: str, max_age: float) -> int:
    ctx = build_context(bus_kind="memory")
    try:
        beats = await ctx.ops.heartbeats()
    finally:
        await ctx.close()
    last = beats.get(service)
    if last is None:
        print(f"{service}: no heartbeat")
        return 1
    age = (ctx.clock.now() - last).total_seconds()
    print(f"{service}: heartbeat {age:.0f}s ago")
    return 0 if age <= max_age else 1


async def _telegram_login() -> int:
    from gold_agent.collectors.telegram import interactive_login
    from gold_agent.core.app import TELEGRAM_SESSION

    ctx = build_context(bus_kind="memory")
    api_id, api_hash = ctx.env.get("TELEGRAM_API_ID"), ctx.env.get("TELEGRAM_API_HASH")
    await ctx.close()
    if not api_id or not api_hash:
        print("Isi TELEGRAM_API_ID dan TELEGRAM_API_HASH di .env dulu (my.telegram.org).")
        return 2
    await interactive_login(int(api_id), api_hash, TELEGRAM_SESSION)
    return 0


async def _labels(args: argparse.Namespace) -> int:
    from datetime import timedelta

    from gold_agent.evaluation.labels import export_template, label_payload, read_labels

    ctx = build_context(bus_kind="memory")
    try:
        if args.labels_command == "export":
            now = ctx.clock.now()
            items = await ctx.news.items_between(
                now - timedelta(days=args.days), now, relevant_only=not args.all
            )
            labelled = {item_id for item_id, _, _ in await ctx.ops.labels()}
            fresh = [i for i in items if i.id not in labelled and not i.backfill]
            count = export_template(fresh, args.out, limit=args.limit)
            print(f"{count} headline ditulis ke {args.out}.")
            print("Isi relevant/direction/impact di file itu, lalu jalankan `labels import`.")
            return 0
        labels, problems = read_labels(args.path)
        now = ctx.clock.now()
        for label in labels:
            await ctx.ops.save_label(label.item_id, args.labeler, label_payload(label), now)
        print(f"{len(labels)} label disimpan; {len(problems)} baris dilewati.")
        for problem in problems[:20]:
            print(f"  - {problem}")
        return 0
    finally:
        await ctx.close()


async def _eval_triage(args: argparse.Namespace) -> int:
    from gold_agent.evaluation.labels import HeadlineLabel
    from gold_agent.evaluation.runner import evaluate
    from gold_agent.evaluation.triage_eval import render_report

    ctx = build_context(bus_kind="memory")
    try:
        rows = await ctx.ops.labels()
        labels = [
            HeadlineLabel.model_validate({"item_id": item_id, **payload})
            for item_id, labeler, payload in rows
            if args.labeler is None or labeler == args.labeler
        ]
        if not labels:
            print("Belum ada label. Jalankan `gold-agent labels export` lalu `labels import`.")
            return 2
        reports = await evaluate(ctx, labels, use_llm=args.llm)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_report(reports), encoding="utf-8")
        for report in reports:
            print(
                f"{report.name}: presisi={report.precision} recall={report.recall} "
                f"arah={report.direction_accuracy} p95={report.latency_p95}"
            )
        print(f"laporan: {args.out}")
        return 0
    finally:
        await ctx.close()


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "verify-sources":
        from gold_agent.ops.verify_cli import main as verify_main

        return verify_main(argv[1:])
    args = _parser().parse_args(argv)
    match args.command:
        case "run":
            return asyncio.run(
                _run(args.services, args.bus, not args.no_migrate, args.dashboard_host)
            )
        case "db":
            return asyncio.run(_db(args.action))
        case "healthcheck":
            return asyncio.run(_healthcheck(args.service, args.max_age))
        case "telegram-login":
            return asyncio.run(_telegram_login())
        case "labels":
            return asyncio.run(_labels(args))
        case "eval-triage":
            return asyncio.run(_eval_triage(args))
        case "report" | "event-study" | "backtest" | "calibrate":
            from gold_agent.cli_eval import HANDLERS

            return asyncio.run(HANDLERS[args.command](args))
        case "mt5-bridge" | "replay":
            from gold_agent.cli_ops import HANDLERS as OPS

            return asyncio.run(OPS[args.command](args))
        case _:
            return 2


if __name__ == "__main__":
    sys.exit(main())

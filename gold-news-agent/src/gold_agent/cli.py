"""`gold-agent` command line."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import structlog

from gold_agent.core.app import AppContext, build_context
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

    db = sub.add_parser("db", help="database maintenance")
    db.add_argument("action", choices=["upgrade"])

    sub.add_parser("telegram-login", help="one-time Telethon login for the channel reader")

    health = sub.add_parser("healthcheck", help="exit 0 if the service heartbeat is fresh")
    health.add_argument("service")
    health.add_argument("--max-age", type=float, default=180.0)

    sub.add_parser("verify-sources", help="Phase 0 source verification", add_help=False)
    return parser


async def _prepare(ctx: AppContext, *, migrate: bool) -> None:
    configure_logging(
        ctx.settings.logging.level, ctx.settings.logging.format, secrets=ctx.env.values()
    )
    if migrate:
        await upgrade(ctx.db.url)
    await ctx.ops.sync_sources(ctx.sources, ctx.clock.now())


async def _run(names: Sequence[str], bus: str | None, migrate: bool) -> int:
    ctx = build_context(bus_kind=bus)
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


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "verify-sources":
        from gold_agent.ops.verify_cli import main as verify_main

        return verify_main(argv[1:])
    args = _parser().parse_args(argv)
    match args.command:
        case "run":
            return asyncio.run(_run(args.services, args.bus, not args.no_migrate))
        case "db":
            return asyncio.run(_db(args.action))
        case "healthcheck":
            return asyncio.run(_healthcheck(args.service, args.max_age))
        case "telegram-login":
            return asyncio.run(_telegram_login())
        case _:
            return 2


if __name__ == "__main__":
    sys.exit(main())

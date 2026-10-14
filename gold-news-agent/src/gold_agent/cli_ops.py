"""Operational commands: the Windows MT5 bridge and the accelerated replay."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Protocol

from gold_agent.core.app import build_context
from gold_agent.core.logging import configure_logging
from gold_agent.core.service import install_signal_handlers, run_with_restart


class SubParsers(Protocol):
    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser: ...


class _BridgeService:
    name = "mt5-bridge"

    def __init__(self, publisher: Any) -> None:
        self._publisher = publisher

    async def run(self, stop: asyncio.Event) -> None:
        await self._publisher.run(stop)


async def mt5_bridge(_args: argparse.Namespace) -> int:
    ctx = build_context(bus_kind="redis")  # REDIS_URL: the core's Redis, over Tailscale/WireGuard
    settings = ctx.settings
    configure_logging(settings.logging.level, settings.logging.format, secrets=ctx.env.values())
    try:
        from gold_agent.market.adapters.factory import mt5_adapter
        from gold_agent.market.bridge_publisher import BridgePublisher

        adapter = mt5_adapter(ctx)
    except ImportError:
        print("Paket MetaTrader5 hanya ada di Windows: jalankan `mt5-bridge` di VPS Windows")
        print("yang menjalankan terminal MT5 (uv sync --extra mt5).")
        await ctx.close()
        return 2
    fx = settings.market.mt5_fx_symbols if settings.market.usd_source == "mt5_synthetic" else {}
    publisher = BridgePublisher(
        adapter, ctx.bus, history=settings.market.history_bars, fx_symbols=fx
    )
    stop = asyncio.Event()
    install_signal_handlers(stop)
    try:
        await run_with_restart(_BridgeService(publisher), stop)
    finally:
        await ctx.close()
    return 0


async def replay(args: argparse.Namespace) -> int:
    from datetime import date, time

    from gold_agent.core.app import DATA_DIR
    from gold_agent.ops.replay import run_replay

    source = build_context(bus_kind="memory")
    scratch = DATA_DIR / "replay"
    await asyncio.to_thread(scratch.mkdir, parents=True, exist_ok=True)
    target = scratch / f"replay-{args.day}.db"
    await asyncio.to_thread(target.unlink, missing_ok=True)
    try:
        summary = await run_replay(
            source,
            date.fromisoformat(args.day),
            database_url=f"sqlite+aiosqlite:///{target}",
            speed=args.speed,
            use_llm=args.llm,
            start_time=time.fromisoformat(args.start) if args.start else None,
            end_time=time.fromisoformat(args.end) if args.end else None,
        )
    finally:
        await source.close()
    print(summary.render())
    print(f"database replay: {target}")
    return 0


def add_parsers(sub: SubParsers) -> None:
    sub.add_parser("mt5-bridge", help="Windows: forward MT5 ticks/history to the core's Redis")
    rep = sub.add_parser("replay", help="replay one recorded WIB day faster than real time")
    rep.add_argument("day", help="WIB date, YYYY-MM-DD")
    rep.add_argument(
        "--speed",
        type=float,
        default=1000.0,
        help="at most x real time (default 1000); results do not depend on it",
    )
    rep.add_argument("--llm", action="store_true", help="use Claude during the replay (costs)")
    rep.add_argument("--from", dest="start", help="WIB HH:MM inside the day (default 00:00)")
    rep.add_argument("--to", dest="end", help="WIB HH:MM inside the day (default 24:00)")


HANDLERS = {"mt5-bridge": mt5_bridge, "replay": replay}

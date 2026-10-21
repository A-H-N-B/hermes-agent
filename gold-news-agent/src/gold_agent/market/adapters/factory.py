"""Choose price adapters from settings, using only verified sources (no unverified URLs)."""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING

import httpx
import structlog

from gold_agent.core.app import AppContext
from gold_agent.market.adapters.base import PriceAdapter
from gold_agent.market.adapters.binance import BinanceAdapter
from gold_agent.market.adapters.bridge import BridgeTickAdapter
from gold_agent.market.adapters.failover import FailoverAdapter
from gold_agent.market.adapters.twelvedata import TwelveDataAdapter

if TYPE_CHECKING:
    from gold_agent.market.adapters.mt5 import Mt5Adapter

log = structlog.get_logger(__name__)


def _verified(ctx: AppContext, *source_ids: str) -> bool:
    production = {s.id for s in ctx.sources.production()}
    missing = [sid for sid in source_ids if sid not in production]
    if missing:
        log.warning("feed.unverified", sources=missing, hint="verify + set status: verified")
    return not missing


def binance_adapter(
    ctx: AppContext,
    client: httpx.AsyncClient,
    *,
    ws_id: str,
    rest_id: str | None,
    name: str,
    symbol: str,
    proxy: bool,
) -> BinanceAdapter | None:
    ids = [ws_id] + ([rest_id] if rest_id else [])
    if not _verified(ctx, *ids):
        return None
    ws = ctx.sources.get(ws_id)
    rest = ctx.sources.get(rest_id) if rest_id else ws
    rest_url = rest.url or ""
    if rest_url.startswith("wss://"):
        rest_url = "https://data-api.binance.vision"
    return BinanceAdapter(
        name=name,
        symbol=symbol,
        ws_url=ws.url or "",
        rest_url=rest_url,
        proxy=proxy,
        clock=ctx.clock,
        client=client,
    )


def _make(ctx: AppContext, client: httpx.AsyncClient, feed: str) -> PriceAdapter | None:
    settings = ctx.settings
    if feed == "binance_paxg":
        return binance_adapter(
            ctx,
            client,
            ws_id="binance_paxg_ws",
            rest_id="binance_paxg_rest",
            name="binance_paxg",
            symbol="PAXGUSDT",
            proxy=True,
        )
    if feed == "twelvedata":
        key = ctx.env.get("TWELVEDATA_API_KEY")
        if not key or not _verified(ctx, "twelvedata_xau_quote"):
            return None
        return TwelveDataAdapter(
            api_key=key,
            symbol=settings.market.twelvedata_symbol,
            poll_seconds=settings.market.twelvedata_poll_seconds,
            clock=ctx.clock,
            client=client,
        )
    if feed == "mt5_bridge":
        return BridgeTickAdapter(ctx.bus)
    if feed == "mt5":
        if not mt5_installed():
            log.warning(
                "feed.mt5_unavailable", hint="Windows + `uv sync --extra mt5`, or mt5_bridge"
            )
            return None
        return mt5_adapter(ctx)
    return None


def mt5_installed() -> bool:
    return sys.platform == "win32" and importlib.util.find_spec("MetaTrader5") is not None


def feed_order(primary: str, fallbacks: tuple[str, ...], *, mt5: bool) -> list[str]:
    """`auto`: the broker's own MT5 price when this computer runs MT5, else Binance PAXG (the
    failover returns to MT5 once its terminal is up). Duplicates are dropped."""
    expanded: list[str] = []
    for feed in (primary, *fallbacks):
        expanded += (
            (["mt5", "binance_paxg"] if mt5 else ["binance_paxg"]) if feed == "auto" else [feed]
        )
    return list(dict.fromkeys(expanded))


def mt5_adapter(ctx: AppContext) -> Mt5Adapter:
    """The local MT5 terminal (Windows only: importing MetaTrader5 fails elsewhere)."""
    from gold_agent.market.adapters.mt5 import Mt5Adapter

    settings = ctx.settings
    offset = settings.price_feed.mt5_server_utc_offset
    login = ctx.env.get("MT5_LOGIN")
    return Mt5Adapter(
        symbol=settings.price_feed.mt5_symbol,
        clock=ctx.clock,
        poll_ms=settings.market.mt5_poll_ms,
        offset_hours=None if offset == "auto" else int(offset),
        login=int(login) if login else None,
        password=ctx.env.get("MT5_PASSWORD"),
        server=ctx.env.get("MT5_SERVER"),
    )


def build_price_adapter(ctx: AppContext, client: httpx.AsyncClient) -> FailoverAdapter | None:
    feed = ctx.settings.price_feed
    feeds = feed_order(feed.primary, feed.fallbacks, mt5=mt5_installed())
    adapters = [a for a in (_make(ctx, client, name) for name in feeds) if a]
    if not adapters:
        log.error("feed.none_available", feeds=feeds)
        return None
    from gold_agent.timeutils.sessions import SessionClock

    sessions = SessionClock(ctx.settings.sessions)
    return FailoverAdapter(
        adapters,
        ctx.clock,
        silence_seconds=max(60.0, ctx.settings.price_feed.stale_tick_seconds * 6),
        market_open=lambda now: sessions.status(now).open,
    )

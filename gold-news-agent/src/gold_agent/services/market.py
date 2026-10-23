"""Market service: price feed -> candles -> context snapshots (+ the dollar and related markets
from MT5, or a USD proxy, and FRED yields)."""

from __future__ import annotations

import asyncio
import hashlib
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog

from gold_agent.collectors.base import sleep_or_stop
from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_ALERTS, STREAM_CONTEXT, STREAM_TICKS
from gold_agent.core.service import run_with_restart
from gold_agent.fundamentals import snapshot as fundamentals
from gold_agent.fundamentals.fetch import FundamentalsFetcher
from gold_agent.fundamentals.snapshot import KV_FUNDAMENTALS
from gold_agent.market.adapters.base import PriceAdapter, TickSink
from gold_agent.market.adapters.factory import binance_adapter, build_price_adapter
from gold_agent.market.candles import CandleBuilder, aggregate
from gold_agent.market.context import WeekendTracker, YieldSnapshot, build_market_context
from gold_agent.market.cross_assets import SPECS, CrossTracker, resolve_symbols
from gold_agent.market.fred import fetch_series
from gold_agent.market.physics import PhysicsInputs, build_physics
from gold_agent.market.usd import DXY_WEIGHTS, UsdTracker
from gold_agent.models import (
    Alert,
    Candle,
    FundamentalSnapshot,
    MarketContext,
    MarketPhysics,
    Tick,
)
from gold_agent.models.common import Timeframe
from gold_agent.timeutils.sessions import SessionClock

log = structlog.get_logger(__name__)
PHYSICS_SECONDS = 60.0  # market-wide econophysics (market/physics.py) is rebuilt this often


class _FeedRunner:
    def __init__(self, name: str, adapter: PriceAdapter, emit: TickSink) -> None:
        self.name = name
        self._adapter = adapter
        self._emit = emit

    async def run(self, stop: asyncio.Event) -> None:
        await self._adapter.run(self._emit, stop)


class MarketService:
    name = "market"

    def __init__(
        self,
        ctx: AppContext,
        *,
        adapter: PriceAdapter | None = None,
        usd_adapter: PriceAdapter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._ctx = ctx
        self._adapter = adapter
        self._usd_adapter = usd_adapter
        self._transport = transport
        self.sessions = SessionClock(ctx.settings.sessions)
        market = ctx.settings.market
        self.builder = CandleBuilder("none", self.sessions, buffer=market.candle_buffer)
        self.last_tick: Tick | None = None
        self.cross = CrossTracker(days=market.usd_history_days)
        self.fundamentals: FundamentalSnapshot | None = None
        self.physics: MarketPhysics | None = None
        self.fetcher = FundamentalsFetcher(ctx.sources, ctx.env)
        self.spreads: deque[tuple[datetime, float]] = deque(maxlen=20_000)
        self.usd: UsdTracker | None = None
        self.yields: YieldSnapshot | None = None
        self.weekend = WeekendTracker()
        self.contract_size: float | None = None
        self.volume_step: float | None = None
        self._last_store: datetime | None = None
        self._last_publish: datetime | None = None
        self._last_context_store: datetime | None = None
        self._pending_ticks: list[Tick] = []
        self._feed: PriceAdapter | None = adapter
        self._stale_alerted = False

    # ------------------------------------------------------------------ ticks

    async def on_tick(self, tick: Tick) -> None:
        self.last_tick = tick
        self.cross.update_gold(tick.ts, tick.mid)
        market = self._ctx.settings.market
        self.spreads.append((tick.received_at, tick.spread))
        status = self.sessions.status(tick.ts)
        if tick.proxy:
            self.weekend.on_tick(tick.mid, status.open)
        if status.open:
            if tick.source != self.builder.source and self.builder.candles("M1"):
                await self._switch_source(tick.source)
            self.builder.source = tick.source
            completed = self.builder.update(tick)
            if completed:
                await self._ctx.market.upsert_candles(completed)
        if self._due(self._last_store, tick.received_at, market.tick_store_min_interval_ms):
            self._last_store = tick.received_at
            self._pending_ticks.append(tick)
            if len(self._pending_ticks) >= 30:
                await self.flush_ticks()
        if self._due(self._last_publish, tick.received_at, market.tick_publish_min_interval_ms):
            self._last_publish = tick.received_at
            await self._ctx.bus.publish(STREAM_TICKS, tick)

    @staticmethod
    def _due(last: datetime | None, now: datetime, interval_ms: int) -> bool:
        return last is None or (now - last).total_seconds() * 1000 >= interval_ms

    async def flush_ticks(self) -> None:
        ticks, self._pending_ticks = self._pending_ticks, []
        await self._ctx.market.insert_ticks(ticks)

    async def _switch_source(self, source: str) -> None:
        """The feed changed instrument (MT5 back after PAXG, or the reverse). The two differ by
        a premium, so mixing their bars would fake a jump in every indicator: start again from
        the new feed's own history."""
        log.warning("market.source_changed", old=self.builder.source, new=source)
        self.builder.reset()
        if self._feed is not None:
            await self.load_history(self._feed)

    # ------------------------------------------------------------------ history

    def _trading_hours_only(self, candles: list[Candle]) -> list[Candle]:
        return [c for c in candles if self.sessions.status(c.open_time).open]

    async def load_history(self, adapter: PriceAdapter) -> None:
        market = self._ctx.settings.market
        h1: list[Candle] = []
        for tf, count in market.history_bars.items():
            try:
                candles = self._trading_hours_only(await adapter.history(tf, count))
            except Exception as exc:
                log.warning("market.history_failed", tf=tf, error=repr(exc)[:200])
                continue
            self.builder.seed(tf, candles)
            await self._ctx.market.upsert_candles([c for c in candles if c.complete])
            if tf == "H1":
                h1 = candles
        for tf in ("H4", "D1"):
            higher_tf: Timeframe = tf
            built = aggregate(h1, higher_tf, self.sessions)
            self.builder.seed(higher_tf, built)
            await self._ctx.market.upsert_candles([c for c in built if c.complete])
        if h1:
            self.builder.source = h1[-1].source
        try:
            self.contract_size = await adapter.contract_size()
            self.volume_step = await adapter.volume_step()
        except Exception as exc:
            log.warning("market.contract_size_failed", error=repr(exc)[:200])
        log.info(
            "market.history_loaded",
            bars={tf: len(self.builder.candles(tf)) for tf in self.builder.timeframes},
        )

    # ------------------------------------------------------------------ context

    def context(self, now: datetime) -> MarketContext:
        snapshot = build_market_context(
            now=now,
            tick=self.last_tick,
            builder=self.builder,
            sessions=self.sessions,
            settings=self._ctx.settings,
            spreads=self.spreads,
            usd=self.usd,
            yields=self.yields,
            weekend=self.weekend,
        )
        return snapshot.model_copy(
            update={
                "contract_size": self.contract_size,
                "volume_step": self.volume_step,
                "cross": self.cross.snapshot(now),
                "fundamental_bias": self.fundamentals.bias if self.fundamentals else None,
                "fundamental_as_of": (
                    self.fundamentals.built_at.isoformat() if self.fundamentals else None
                ),
                "physics": self.physics,
            }
        )

    async def publish_context(self) -> MarketContext:
        now = self._ctx.clock.now()
        completed = self.builder.close_due(now)
        if completed:
            await self._ctx.market.upsert_candles(completed)
        snapshot = self.context(now)
        await self._ctx.bus.publish(STREAM_CONTEXT, snapshot)
        every = self._ctx.settings.market.context_store_seconds
        if (
            self._last_context_store is None
            or (now - self._last_context_store).total_seconds() >= every
        ):
            self._last_context_store = now
            await self._ctx.market.save_context(snapshot)
            await self.flush_ticks()
        await self._stale_alert(snapshot)
        return snapshot

    async def _stale_alert(self, snapshot: MarketContext) -> None:
        stale_now = snapshot.market_open and snapshot.stale
        if stale_now == self._stale_alerted:
            return
        self._stale_alerted = stale_now
        now = snapshot.ts
        key = f"stale_price:{now:%Y%m%d%H%M}"
        alert = Alert(
            id=hashlib.sha1(key.encode()).hexdigest()[:16],  # noqa: S324
            kind="stale_price" if stale_now else "recovery",
            severity="critical" if stale_now else "info",
            title="Data harga basi" if stale_now else "Data harga pulih",
            detail=(
                f"Tidak ada tick selama {snapshot.tick_age_seconds or 0:.0f} dtk saat pasar buka."
                if stale_now
                else f"Tick kembali mengalir dari {snapshot.source}."
            ),
            created_at=now,
            dedupe_key="stale_price",
        )
        await self._ctx.bus.publish(STREAM_ALERTS, alert)

    async def _context_loop(self, stop: asyncio.Event) -> None:
        interval = self._ctx.settings.infra.context_interval_seconds
        while not await self._ctx.clock.wait(stop, interval):
            try:
                await self.publish_context()
            except Exception:
                log.exception("market.context_failed")

    # ------------------------------------------------------------------ econophysics

    def _physics_inputs(self, now: datetime) -> PhysicsInputs:
        """Copies of the series the loop keeps updating, for the worker thread."""
        related = {
            key: list(series.points) for key, series in self.cross.series.items() if key in SPECS
        }
        daily = [c for c in self.builder.candles("D1") if c.complete]
        return PhysicsInputs(
            now=now,
            gold=list(self.cross.gold.points),
            related=related,
            labels={key: SPECS[key].label for key in related},
            daily=[c.close for c in daily],
            daily_as_of=daily[-1].open_time.isoformat() if daily else None,
        )

    async def refresh_physics(self) -> MarketPhysics:
        inputs = self._physics_inputs(self._ctx.clock.now())
        self.physics = await asyncio.to_thread(build_physics, inputs, self.physics)
        return self.physics

    async def _physics_loop(self, stop: asyncio.Event) -> None:
        while not await self._ctx.clock.wait(stop, PHYSICS_SECONDS):
            try:
                await self.refresh_physics()
            except Exception:
                log.exception("market.physics_failed")

    async def _seed_gold_minutes(self) -> None:
        """Gold's minute series from stored M1 bars (the aftershock read needs days of it; with
        MT5 the broker's own history fills in what is older, market/cross_assets.py)."""
        now = self._ctx.clock.now()
        start = now - timedelta(days=self._ctx.settings.market.usd_history_days)
        bars = await self._ctx.market.candles("M1", start, now, source=self.builder.source)
        self.cross.gold.seed([(c.open_time, c.close) for c in bars if c.complete])

    # ------------------------------------------------------------------ macro feeds

    async def _usd_tick(self, tick: Tick) -> None:
        if self.usd is not None:
            self.usd.update_eurusd(tick.ts, tick.mid)

    async def _seed_usd(self, adapter: PriceAdapter) -> None:
        if self.usd is None:
            return
        minutes = min(self._ctx.settings.market.usd_history_days * 1440, 7200)
        try:
            for candle in await adapter.history("M1", minutes):
                self.usd.update_eurusd(candle.close_time - timedelta(seconds=1), candle.close)
        except Exception as exc:
            log.warning("market.usd_history_failed", error=repr(exc)[:200])

    async def _fred_loop(self, client: httpx.AsyncClient, stop: asyncio.Event) -> None:
        ctx = self._ctx
        production = {s.id for s in ctx.sources.production()}
        wanted = {"fred_dgs10": "DGS10", "fred_dfii10": "DFII10"}
        if not ctx.env.get("FRED_API_KEY") or not set(wanted) & production:
            log.warning("market.fred_unavailable", hint="FRED_API_KEY + verified fred_* sources")
            return
        while not stop.is_set():
            readings = {}
            for source_id, series in wanted.items():
                if source_id not in production:
                    continue
                try:
                    readings[series] = await fetch_series(
                        client, ctx.sources.get(source_id), ctx.env, series
                    )
                except Exception as exc:
                    log.warning(
                        "market.fred_failed", series=series, error=ctx.redact(repr(exc))[:200]
                    )
            nominal, real = readings.get("DGS10"), readings.get("DFII10")
            if nominal or real:
                as_of = max(r.as_of for r in (nominal, real) if r is not None)
                self.yields = YieldSnapshot(
                    us10y=nominal.value if nominal else None,
                    us10y_change_1d=nominal.change_1d if nominal else None,
                    real10y=real.value if real else None,
                    real10y_change_1d=real.change_1d if real else None,
                    as_of=as_of.isoformat(),
                )
            if await sleep_or_stop(stop, ctx.settings.market.fred_refresh_minutes * 60):
                return

    async def refresh_fundamentals(self, client: httpx.AsyncClient) -> FundamentalSnapshot:
        """Official yields, positioning and ETF data (each at its own pace), our own release
        surprises and geopolitical news, and the dollar's 5-day move: the day's bias."""
        ctx = self._ctx
        now = ctx.clock.now()
        await self.fetcher.refresh(client, now)
        inputs = self.fetcher.inputs
        inputs.releases = await ctx.news.releases_before(now, 200)
        inputs.analyses = await ctx.news.analyses_between(now - timedelta(days=15), now)
        dollar = self.cross.series.get("DXY")
        inputs.dollar_5d_pct = dollar.change_pct(now, 5 * 24 * 60) if dollar else None
        inputs.dollar_source = self.cross.symbols.get("DXY")
        snapshot = fundamentals.build_snapshot(inputs, now)
        self.fundamentals = snapshot
        await ctx.ops.kv_set(KV_FUNDAMENTALS, snapshot.model_dump(mode="json"), now)
        treasury = self._treasury_yields()
        if treasury is not None and (
            self.yields is None or (treasury.as_of or "") >= (self.yields.as_of or "")
        ):
            self.yields = treasury
        return snapshot

    def _treasury_yields(self) -> YieldSnapshot | None:
        """The context's 10-year yields from Treasury when FRED (key needed) is not set up."""
        nominal = fundamentals.change(fundamentals.series(self.fetcher.inputs.nominal, "10Y"), 1)
        if nominal is None:
            return None
        real = fundamentals.change(fundamentals.series(self.fetcher.inputs.real, "10Y"), 1)
        return YieldSnapshot(
            us10y=nominal[0],
            us10y_change_1d=round(nominal[1], 4),
            real10y=real[0] if real else None,
            real10y_change_1d=round(real[1], 4) if real else None,
            as_of=nominal[2].isoformat(),
        )

    async def _fundamentals_loop(self, client: httpx.AsyncClient, stop: asyncio.Event) -> None:
        every = self._ctx.settings.market.fundamentals_refresh_minutes * 60
        while not stop.is_set():
            try:
                await self.refresh_fundamentals(client)
            except Exception:
                log.exception("market.fundamentals_failed")
            if await sleep_or_stop(stop, every):
                return

    def _mt5_feed(self) -> Any:
        """The MT5 adapter among the price feeds, if this computer runs MT5."""
        feed = self._feed
        finder = getattr(feed, "find", None)
        mt5 = finder("mt5") if callable(finder) else feed
        return mt5 if callable(getattr(mt5, "symbol_list", None)) else None

    def _resolve_cross(self, mt5: Any) -> dict[str, str]:
        overrides = self._ctx.settings.market.mt5_symbols
        symbols = resolve_symbols(mt5.symbol_list(), overrides)
        self.cross.symbols = dict(symbols)
        self.cross.basket = {pair for pair in DXY_WEIGHTS if pair in symbols}
        if self.usd is not None:
            self.usd.source = "DXY broker (MT5)" if "DXY" in symbols else "DXY sintetis (MT5)"
        log.info("market.cross_symbols", symbols=symbols)
        return symbols

    async def _seed_cross(self, mt5: Any, symbols: dict[str, str]) -> None:
        minutes = self._ctx.settings.market.usd_history_days * 24 * 60
        history = {
            key: await asyncio.to_thread(mt5.closes, name, minutes) for key, name in symbols.items()
        }
        self.cross.seed(history)
        self.cross.gold.seed(await asyncio.to_thread(mt5.closes, mt5.gold_symbol, minutes))

    async def _cross_loop(self, stop: asyncio.Event) -> None:
        """The dollar and related markets from the broker's MT5 (market/cross_assets.py):
        symbols found once, a few days of minute history, then a quote every 5 s. While MT5
        is closed or logged out it waits and tries again every minute."""
        mt5 = self._mt5_feed()
        if mt5 is None:
            return
        symbols: dict[str, str] | None = None
        failing = False
        while not stop.is_set():
            try:
                if symbols is None:
                    symbols = await asyncio.to_thread(self._resolve_cross, mt5)
                    await self._seed_cross(mt5, symbols)
                quotes = await asyncio.to_thread(mt5.quotes, symbols)
                now = self._ctx.clock.now()
                dxy = self.cross.update(now, quotes)
                if dxy is not None and self.usd is not None:
                    self.usd.update(now, dxy)
                failing = False
            except Exception as exc:
                if not failing:
                    log.warning("market.cross_unavailable", error=repr(exc)[:200])
                failing, symbols = True, None
            if await sleep_or_stop(stop, 60 if failing else 5):
                return

    # ------------------------------------------------------------------ run

    async def run(self, stop: asyncio.Event) -> None:
        ctx = self._ctx
        async with httpx.AsyncClient(
            headers={"User-Agent": ctx.settings.http.full_user_agent()},
            timeout=ctx.settings.http.timeout_seconds,
            transport=self._transport,
        ) as client:
            adapter = self._adapter or build_price_adapter(ctx, client)
            self._feed = adapter
            usd_mode = ctx.settings.market.usd_source
            if usd_mode == "auto":  # the broker's own FX pairs when MT5 runs here
                usd_mode = "mt5_synthetic" if self._mt5_feed() is not None else "binance_eurusdt"
            usd_adapter = self._usd_adapter
            if usd_mode == "binance_eurusdt" and usd_adapter is None:
                usd_adapter = binance_adapter(
                    ctx,
                    client,
                    ws_id="binance_eurusdt_ws",
                    rest_id="binance_paxg_rest",
                    name="binance_eurusdt",
                    symbol="EURUSDT",
                    proxy=True,
                )
            if usd_mode == "binance_eurusdt" and usd_adapter is not None:
                self.usd = UsdTracker(
                    source="EURUSDT (proxy)", history_days=ctx.settings.market.usd_history_days
                )
            elif usd_mode == "mt5_synthetic":
                self.usd = UsdTracker(
                    source="DXY sintetis (MT5)", history_days=ctx.settings.market.usd_history_days
                )

            tasks: list[asyncio.Task[None]] = [asyncio.create_task(self._context_loop(stop))]
            if adapter is not None:
                await self.load_history(adapter)
                await self._seed_gold_minutes()
                tasks.append(
                    asyncio.create_task(
                        run_with_restart(_FeedRunner("price-feed", adapter, self.on_tick), stop)
                    )
                )
            else:
                log.error(
                    "market.no_price_feed", hint="context will report 'data harga tidak tersedia'"
                )
            if usd_adapter is not None and usd_mode == "binance_eurusdt":
                await self._seed_usd(usd_adapter)
                tasks.append(
                    asyncio.create_task(
                        run_with_restart(_FeedRunner("usd-feed", usd_adapter, self._usd_tick), stop)
                    )
                )
            if self._mt5_feed() is not None:
                tasks.append(asyncio.create_task(self._cross_loop(stop)))
            tasks.append(asyncio.create_task(self._fred_loop(client, stop)))
            tasks.append(asyncio.create_task(self._fundamentals_loop(client, stop)))
            tasks.append(asyncio.create_task(self._physics_loop(stop)))
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    task.cancel()
                await self.flush_ticks()

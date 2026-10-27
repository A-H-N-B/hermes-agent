"""Load evaluation inputs from the archive (prices, replay data, engine-state samples)."""

from __future__ import annotations

from datetime import datetime, timedelta

from gold_agent.core.app import AppContext
from gold_agent.evaluation.backtest import Replay
from gold_agent.evaluation.calibration import StateSample
from gold_agent.market.series import PriceSeries
from gold_agent.models import Candle


async def main_source(ctx: AppContext, tf: str, since: datetime) -> str | None:
    """The price source with the most bars: one consistent history, never a mix of feeds."""
    counts = await ctx.market.candle_sources(tf, since)
    return max(counts, key=lambda name: counts[name]) if counts else None


async def load_bars(
    ctx: AppContext, start: datetime, end: datetime, source: str | None = None
) -> list[Candle]:
    source = source or await main_source(ctx, "M1", start)
    if source is None:
        return []
    return await ctx.market.candles("M1", start, end, source=source)


async def load_prices(ctx: AppContext, start: datetime, end: datetime) -> PriceSeries:
    return PriceSeries.from_candles(await load_bars(ctx, start, end))


async def load_replay(ctx: AppContext, start: datetime, end: datetime) -> Replay:
    contexts = await ctx.market.contexts_between(start, end)
    sources = [c.source for c in contexts if c.source]
    source = max(set(sources), key=sources.count) if sources else None
    contexts = [c for c in contexts if c.source in (source, None)]
    return Replay(
        contexts=contexts,
        versions=await ctx.news.analysis_versions_between(start - timedelta(days=1), end),
        events=await ctx.calendar.between(start - timedelta(days=1), end + timedelta(days=1)),
        bars=await load_bars(ctx, start, end + timedelta(days=1), source),
    )


async def load_state_samples(
    ctx: AppContext, start: datetime, end: datetime, *, horizon_minutes: int = 60
) -> list[StateSample]:
    """Engine states paired with the gold move that followed (for weight calibration)."""
    prices = await load_prices(ctx, start, end + timedelta(minutes=horizon_minutes + 5))
    samples: list[StateSample] = []
    for state in await ctx.signals.states_between(start, end):
        now_price = prices.at(state.ts)
        later = prices.at(state.ts + timedelta(minutes=horizon_minutes))
        if now_price is None or later is None or state.components.session_factor == 0:
            continue
        c = state.components
        samples.append(
            StateSample(
                ts=state.ts,
                components={"N": c.N, "M": c.M, "T": c.T, "P": c.P},
                forward=later - now_price,
            )
        )
    return samples

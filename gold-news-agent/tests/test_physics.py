"""Market-wide econophysics (market/physics.py) and its place in the market context: built from
copies of the minute series, the bubble read reused until a new daily bar."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import numpy as np

from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.dashboard.indicators_view import indicator_summary, timeframe_read
from gold_agent.market.physics import PhysicsInputs, build_physics
from gold_agent.market.physics_read import signal_note
from gold_agent.models import (
    Aftershock,
    BubbleRisk,
    Candle,
    Leader,
    MarketPhysics,
    TimeframeState,
)
from gold_agent.services.market import MarketService
from tests.builders import market

T0 = datetime(2026, 10, 5, tzinfo=UTC)
MINUTES = 7200


def markets(seed: int = 1) -> dict[str, list[tuple[datetime, float]]]:
    """Gold, silver whose own moves lead gold's by one minute, the dollar moving against both
    at once (no lead), and an unrelated index; gold's volatility clusters (GARCH)."""
    rng = np.random.default_rng(seed)
    factor = rng.standard_normal(MINUTES)
    own = rng.standard_normal(MINUTES)  # silver's own news: gold catches up a minute later
    silver = 0.7 * factor + 0.7 * own
    var, noise = 1.0, []
    for shock in rng.standard_normal(MINUTES):
        noise.append(math.sqrt(var) * shock)
        var = 0.02 + 0.1 * noise[-1] ** 2 + 0.88 * var
    gold = 0.6 * factor + 0.3 * np.concatenate([[0.0], own[:-1]]) + 0.6 * np.array(noise)
    dollar = -0.6 * factor + 0.8 * rng.standard_normal(MINUTES)
    index = rng.standard_normal(MINUTES)

    def series(start: float, moves: np.ndarray, scale: float) -> list[tuple[datetime, float]]:
        prices = start * np.exp(np.cumsum(scale * moves))
        return [(T0 + timedelta(minutes=i), float(p)) for i, p in enumerate(prices)]

    return {
        "gold": series(2650.0, gold, 3e-4),
        "XAGUSD": series(31.0, silver, 5e-4),
        "DXY": series(103.0, dollar, 1e-4),
        "US500": series(5800.0, index, 2e-4),
    }


def bubble_closes() -> list[float]:
    rng = np.random.default_rng(2)
    days = np.arange(300, dtype=float)
    critical = 299 + 15
    f = (critical - days) ** 0.5
    log_price = -0.03 * f + 0.003 * f * np.cos(8 * np.log(critical - days))
    return list(4000 * np.exp(log_price + 0.005 * rng.standard_normal(300)))


def inputs(daily_as_of: str = "2026-10-09") -> PhysicsInputs:
    series = markets()
    return PhysicsInputs(
        now=series["gold"][-1][0],
        gold=series["gold"],
        related={key: points for key, points in series.items() if key != "gold"},
        labels={"XAGUSD": "Perak (XAGUSD)", "DXY": "Dolar AS (DXY)", "US500": "S&P 500"},
        daily=bubble_closes(),
        daily_as_of=daily_as_of,
    )


def test_physics_reads_aftershocks_the_leader_the_common_factor_and_the_bubble() -> None:
    physics = build_physics(inputs())
    assert physics.aftershock is not None and physics.aftershock.clustered
    assert 0 < physics.aftershock.next_hour < 1 and physics.aftershock.minutes == MINUTES
    by_key = {leader.key: leader for leader in physics.leaders}
    silver = by_key["XAGUSD"]
    assert silver.leads and silver.corr > 0 and physics.leaders[0].key == "XAGUSD"
    assert silver.last_move_pct is not None
    assert silver.implied == (1 if silver.last_move_pct > 0 else -1)  # gold follows silver
    assert not by_key["DXY"].leads and by_key["DXY"].implied == 0  # same-minute, not ahead
    assert not by_key["US500"].leads
    assert physics.common is not None and physics.common.factors >= 1
    assert physics.common.gold_loading > 0.3 and physics.common.markets == 4
    assert physics.bubble is not None and physics.bubble.bubble >= 0.3
    assert physics.bubble.as_of == "2026-10-09"


def test_the_bubble_read_waits_for_a_new_daily_bar_and_thin_data_reads_nothing() -> None:
    kept = BubbleRisk(bubble=0.9, anti_bubble=0.0, windows=20, as_of="2026-10-09")
    previous = MarketPhysics(built_at=T0, bubble=kept)
    assert build_physics(inputs(), previous).bubble == kept  # same daily bar: reused
    assert build_physics(inputs("2026-10-12"), previous).bubble != kept  # a new one: rebuilt
    thin = build_physics(
        PhysicsInputs(now=T0, gold=[], related={}, labels={}, daily=[4000.0] * 20, daily_as_of=None)
    )
    assert thin.aftershock is None and thin.leaders == [] and thin.common is None
    assert thin.bubble is None


async def test_the_market_context_carries_physics_built_from_its_own_series(
    ctx_factory: Callable[..., AppContext], clock: ManualClock
) -> None:
    context = ctx_factory()
    await context.db.create_all()
    try:
        service = MarketService(context)
        series = markets()
        stored = [
            Candle(
                source="mt5",
                tf="M1",
                open_time=ts,
                close_time=ts + timedelta(minutes=1),
                open=price,
                high=price,
                low=price,
                close=price,
                complete=True,
            )
            for ts, price in series["gold"][-3000:]
        ]
        await context.market.upsert_candles(stored)
        service.builder.source = "mt5"
        clock.set(series["gold"][-1][0] + timedelta(minutes=1))
        await service._seed_gold_minutes()  # pyright: ignore[reportPrivateUsage]
        assert len(service.cross.gold.points) == 3000  # stored M1 bars seed the minute series
        for key in ("XAGUSD", "DXY", "US500"):
            service.cross.series_for(key).seed(series[key])
        service.cross.gold.seed(series["gold"])  # older minutes fill in
        physics = await service.refresh_physics()
        assert physics.aftershock is not None and physics.leaders
        assert service.context(clock.now()).physics == physics
    finally:
        await context.close()


def test_the_readings_reach_the_dashboard_and_signals_in_plain_words() -> None:
    noisy = TimeframeState(tf="M15", close=2650, tail_alpha=2.8, tail_nu=4.0, entropy=0.995)
    pulled = TimeframeState(
        tf="M5", close=2650, entropy=0.95, ou_t=-3.4, ou_half_life=12, ou_mean=2655.5
    )
    rows = {row["label"]: row for row in timeframe_read(noisy)["physics"]}
    assert rows["Ekor"]["tag"] == "sangat tebal" and rows["Ekor"]["tone"] == "warn"
    assert rows["Keteraturan"]["tag"] == "hampir acak"
    reverting = {row["label"]: row for row in timeframe_read(pulled)["physics"]}
    assert (
        reverting["Mean reversion"]["tag"] == "nyata"
        and "2655.50" in reverting["Mean reversion"]["detail"]
    )
    assert reverting["Keteraturan"]["tone"] == "ok"
    physics = MarketPhysics(
        built_at=T0,
        aftershock=Aftershock(
            branching=0.6,
            clustered=True,
            intensity_ratio=2.6,
            next_hour=0.55,
            half_life_minutes=12,
            events=80,
            minutes=7200,
            threshold_pct=0.08,
        ),
        leaders=[
            Leader(
                key="XAGUSD",
                label="Perak (XAGUSD)",
                bits=0.01,
                reverse_bits=0.0,
                z=4.0,
                corr=0.1,
                leads=True,
                last_move_pct=-0.04,
                implied=-1,
            )
        ],
    )
    summary = indicator_summary(market(2650.0).model_copy(update={"physics": physics}))
    market_rows = {row["label"]: row for row in summary["physics"]}
    assert market_rows["Badai susulan (Hawkes)"]["tag"] == "berlangsung"
    assert market_rows["Gerak besar 1 jam ke depan"]["tone"] == "warn"
    assert market_rows["Pemimpin emas"]["tone"] == "down"
    assert indicator_summary(None)["physics"] == []
    note = signal_note(noisy, physics)
    assert note is not None and "keteraturan hampir acak" in note and "ekor sangat tebal" in note
    assert "Pemimpin emas Perak (XAGUSD): emas cenderung turun" in note
    assert signal_note(TimeframeState(tf="M5"), None) is None

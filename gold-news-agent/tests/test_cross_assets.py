"""Related markets from the broker (market/cross_assets.py): names found the way a trader would,
a dollar index from whatever pairs the broker has, and each market counting only as much as
its measured link with gold supports."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from gold_agent.market.cross_assets import (
    SPECS,
    CrossTracker,
    correlation,
    dollar_index,
    relevance,
    resolve_symbols,
)
from gold_agent.market.minutes import MinuteSeries
from gold_agent.market.usd import synthetic_dxy

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SIX = {
    "EURUSD": 1.08,
    "USDJPY": 150.0,
    "GBPUSD": 1.27,
    "USDCAD": 1.36,
    "USDSEK": 10.5,
    "USDCHF": 0.88,
}


def symbol(name: str, *, visible: bool = True) -> Any:
    return SimpleNamespace(name=name, visible=visible)


def test_broker_names_are_found_with_their_suffixes_and_market_watch_first() -> None:
    listed = [
        symbol("XAUUSDm"),
        symbol("XAGUSD", visible=False),
        symbol("XAGUSDm"),
        symbol("SILVEREUR"),  # silver in euros: not the one
        symbol("XTIUSD"),
        symbol("CLF"),  # a share, not crude
        symbol("US500.cash"),
        symbol("EURUSDm"),
        symbol("USDJPYm"),
        symbol("GBPUSD"),
        symbol("UST10Y"),
    ]
    found = resolve_symbols(listed, overrides={"VIX": "VOLX.f"})
    assert found == {
        "XAGUSD": "XAGUSDm",
        "WTI": "XTIUSD",
        "US500": "US500.cash",
        "US10Y": "UST10Y",
        "USDJPY": "USDJPYm",
        "EURUSD": "EURUSDm",
        "GBPUSD": "GBPUSD",
        "VIX": "VOLX.f",  # the user's setting wins
    }


def test_the_dollar_index_uses_the_pairs_the_broker_has() -> None:
    value, used = dollar_index(SIX)
    assert used == 6 and value == pytest.approx(synthetic_dxy(SIX))
    partial, used = dollar_index({"EURUSD": 1.08, "USDJPY": 150.0})
    assert used == 2 and partial is not None
    assert dollar_index({"USDJPY": 150.0}) == (None, 0)  # EURUSD (58% of DXY) is required
    tracker = CrossTracker(days=1)
    tracker.basket = {"EURUSD", "USDJPY"}
    assert tracker.update(T0, {"EURUSD": 1.08, "USDJPY": 150.0}) == pytest.approx(partial)
    assert tracker.symbols["DXY"] == "sintetis 2/6 pasangan"
    # A pair missing from one poll: no value rather than a jump in the index level.
    assert tracker.update(T0 + timedelta(minutes=1), {"EURUSD": 1.07}) is None


def test_a_bond_futures_price_is_turned_into_the_yield_direction() -> None:
    tracker = CrossTracker(days=1)
    tracker.update(T0, {"US10Y": 110.0})
    tracker.update(T0 + timedelta(minutes=1), {"US10Y": 109.0})  # price down = yield up
    series = tracker.series["US10Y"]
    assert series.points[-1][1] > series.points[0][1]
    yields = CrossTracker(days=1)
    yields.update(T0, {"US10Y": 4.21})  # already a yield: kept as is
    assert yields.series["US10Y"].latest == 4.21 and "US10Y" not in yields.inverted


def test_each_market_counts_as_much_as_its_measured_link_with_gold() -> None:
    silver, dollar, stocks = SPECS["XAGUSD"], SPECS["DXY"], SPECS["US500"]
    assert relevance(silver, None, 0)[:2] == (1, 1.0)  # the usual link, until data says more
    sign, weight, _ = relevance(dollar, 0.5, 2000)  # gold and the dollar rising together
    assert (sign, weight) == (-1, 0.0)  # a broken link counts for nothing, never flipped
    assert relevance(stocks, None, 0)[1] == 0.0  # no stable rule and no data: nothing
    sign, weight, corr = relevance(stocks, -0.4, 2000)
    assert sign == -1 and weight > 0.9 and corr < -0.3  # the data shows one: it follows it


def walk(start: float, steps: list[float]) -> list[float]:
    values = [start]
    for step in steps:
        values.append(values[-1] * math.exp(step))
    return values


def test_correlation_is_measured_on_aligned_five_minute_returns() -> None:
    steps = [0.001 * math.sin(i * 1.7) + 0.0004 * ((i * 7) % 5 - 2) for i in range(600)]
    gold, silver, dollar = MinuteSeries(3), MinuteSeries(3), MinuteSeries(3)
    for i, (g, s, d) in enumerate(
        zip(walk(2650, steps), walk(31, steps), walk(104, [-x for x in steps]), strict=True)
    ):
        ts = T0 + timedelta(minutes=i)
        gold.update(ts, g)
        silver.update(ts, s)
        dollar.update(ts, d)
    up, pairs = correlation(silver, gold, 5)
    down, _ = correlation(dollar, gold, 5)
    assert pairs == 120 and up == pytest.approx(1.0) and down == pytest.approx(-1.0)


def test_a_sharp_dollar_rally_pulls_gold_down_and_a_silver_rally_lifts_it() -> None:
    tracker = CrossTracker(days=2)
    tracker.basket = {"EURUSD"}
    quiet = [0.0002 * (1 if i % 2 else -1) for i in range(400)]
    history: dict[str, list[tuple[datetime, float]]] = {}
    for key, start in (("EURUSD", 1.08), ("XAGUSD", 31.0)):
        history[key] = [(T0 + timedelta(minutes=i), v) for i, v in enumerate(walk(start, quiet))]
    tracker.seed(history)
    now = T0 + timedelta(minutes=400)
    tracker.update(now, {"EURUSD": 1.08 * math.exp(-0.004), "XAGUSD": 31.0 * math.exp(0.01)})
    cross = tracker.snapshot(now)
    assert cross["DXY"].z_15m is not None and cross["DXY"].z_15m > 3
    assert cross["DXY"].effect is not None and cross["DXY"].effect < -0.5
    assert cross["XAGUSD"].effect is not None and cross["XAGUSD"].effect > 0.5
    assert cross["DXY"].symbol == "sintetis 1/6 pasangan"


class BrokerTerminal:
    """The slice of the MT5 adapter the market service uses for related markets."""

    name = "mt5"
    gold_symbol = "XAUUSDm"

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.prices = {"XAGUSDm": 31.0, "EURUSDm": 1.08, "USDJPYm": 150.0, "XAUUSDm": 2650.0}

    def symbol_list(self) -> list[Any]:
        return [symbol(name) for name in self.prices]

    def quotes(self, symbols: dict[str, str]) -> dict[str, float]:
        return {key: self.prices[name] for key, name in symbols.items() if name in self.prices}

    def closes(self, name: str, count: int) -> list[tuple[datetime, float]]:
        wiggle = [0.0003 * (1 if i % 2 else -1) for i in range(min(count, 300))]
        start = self.now - timedelta(minutes=len(wiggle))
        return [
            (start + timedelta(minutes=i), v) for i, v in enumerate(walk(self.prices[name], wiggle))
        ]


async def test_the_market_service_reads_related_markets_and_the_dollar_from_mt5(
    ctx_factory: Any, clock: Any
) -> None:
    from gold_agent.services.market import MarketService

    context = ctx_factory()
    await context.db.create_all()
    try:
        service = MarketService(context)
        terminal = BrokerTerminal(clock.now())
        service.usd = None
        symbols = service._resolve_cross(terminal)  # pyright: ignore[reportPrivateUsage]
        assert symbols == {"XAGUSD": "XAGUSDm", "EURUSD": "EURUSDm", "USDJPY": "USDJPYm"}
        await service._seed_cross(terminal, symbols)  # pyright: ignore[reportPrivateUsage]
        assert len(service.cross.gold.points) == 301
        terminal.prices["XAGUSDm"] = 31.6  # silver jumps
        service.cross.update(clock.now(), terminal.quotes(symbols))
        cross = service.context(clock.now()).cross
        assert set(cross) == {"DXY", "USDJPY", "XAGUSD"}
        assert cross["DXY"].symbol == "sintetis 2/6 pasangan"
        assert cross["XAGUSD"].effect is not None and cross["XAGUSD"].effect > 0.5
    finally:
        await context.close()

"""Markets that move gold, read live from the broker's MT5 terminal (section 9, extended).

Gold reacts within minutes to the dollar, US yields and risk appetite, and moves with silver.
The broker's own quotes are the fastest free source of those, so every related instrument the
broker lists is tracked per minute: its 15-minute and 1-hour moves as z-scores against its own
recent history, and its correlation with gold (5-minute returns over the last days). The macro
score uses that correlation, shrunk towards the usual relationship, to decide how much each one
counts now, so a link that broke down fades out instead of misleading the score, and an asset
with no stable rule (equities) counts only when the data shows one.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from statistics import fmean, pstdev
from typing import Any

from gold_agent.market.usd import DXY_CONSTANT, DXY_WEIGHTS
from gold_agent.models import CrossAsset

CORR_FULL = 0.3  # a correlation this strong (in the expected direction) counts fully
CORR_PRIOR_PAIRS = 150  # the usual relationship weighs like this many observed return pairs
CORR_STEP_MINUTES = 5
CORR_MIN_PAIRS = 30
YIELD_MAX = 20.0  # a "10-year" quote above this is a bond futures price, not a yield
_SUFFIXES = {"CASH", "SPOT", "PRO", "ECN", "STD", "MICRO", "MINI", "M", "I", "C", "R", "X"}


@dataclass(frozen=True)
class AssetSpec:
    key: str
    label: str  # Indonesian, for the dashboard and the AI brief
    patterns: tuple[str, ...]  # names brokers give it (suffixes like m, .r, Cash allowed)
    sign: int  # how gold usually moves when this rises: +1, -1, or 0 (no stable rule)
    prior_corr: float  # the usual 5-minute return correlation with gold
    weight: float  # share of the intraday macro score
    why: str


ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec(
        "DXY",
        "Dolar AS (DXY)",
        ("DXY", "USDX", "DX", "USDOLLAR", "USDIDX"),
        -1,
        -0.45,
        0.40,
        "dolar menguat membuat emas (dihargai dalam dolar) cenderung turun",
    ),
    AssetSpec(
        "US10Y",
        "Yield obligasi AS 10 tahun",
        ("US10Y", "UST10Y", "USTN10Y", "10YUST", "TNOTE", "TNX"),
        -1,
        -0.30,
        0.20,
        "yield naik menaikkan biaya memegang emas yang tidak berbunga",
    ),
    AssetSpec(
        "USDJPY",
        "USDJPY (cermin yield AS)",
        ("USDJPY",),
        -1,
        -0.30,
        0.10,
        "USDJPY naik biasanya bersama yield AS yang naik",
    ),
    AssetSpec(
        "XAGUSD",
        "Perak (XAGUSD)",
        ("XAGUSD", "SILVER", "XAG"),
        1,
        0.75,
        0.10,
        "logam mulia bergerak bersama; perak sering bergerak lebih dulu",
    ),
    AssetSpec(
        "WTI",
        "Minyak WTI",
        ("XTIUSD", "USOIL", "WTI", "USOUSD", "OILUSD", "CRUDE", "CL"),
        1,
        0.15,
        0.05,
        "minyak naik mendorong ekspektasi inflasi",
    ),
    AssetSpec(
        "US500",
        "Saham AS (S&P 500)",
        ("US500", "SPX500", "SP500", "USA500", "US500CASH", "SPX"),
        0,
        0.0,
        0.08,
        "risk-on/risk-off; arah hubungannya berubah-ubah, jadi diukur dari data",
    ),
    AssetSpec(
        "VIX",
        "Indeks ketakutan (VIX)",
        ("VIX", "VOLX", "USVIX"),
        1,
        0.05,
        0.07,
        "pasar takut mendorong permintaan aset aman",
    ),
)
SPECS = {spec.key: spec for spec in ASSETS}


# ------------------------------------------------------------------ broker symbols


def _suffix_ok(rest: str) -> bool:
    """What may follow a known name at a broker: nothing, a separator (XAGUSD.r, US500_cash),
    a lowercase tag (XAGUSDm, US500pro) or a known plain tag (US500CASH)."""
    if not rest:
        return True
    if rest[0] in "._#-+!":
        return True
    return (rest.islower() and rest.isalpha()) or rest.upper() in _SUFFIXES


def _rank(info: Any, patterns: Sequence[str]) -> tuple[int, int, int, str] | None:
    name = str(getattr(info, "name", ""))
    upper = name.upper()
    for index, pattern in enumerate(patterns):
        if upper.startswith(pattern) and _suffix_ok(name[len(pattern) :]):
            hidden = 0 if getattr(info, "visible", False) else 1
            return hidden, index, len(name), name
    return None


def pick_symbol(symbols: Iterable[Any], patterns: Sequence[str]) -> str | None:
    """The broker's name for an instrument, preferring Market Watch, then the usual name."""
    ranked = sorted(rank for rank in (_rank(s, patterns) for s in symbols) if rank is not None)
    return ranked[0][-1] if ranked else None


def resolve_symbols(
    symbols: Sequence[Any], overrides: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Logical name -> broker symbol for every related market and DXY basket pair the broker
    lists. `overrides` (settings) win for brokers with unusual names."""
    found: dict[str, str] = {}
    wanted = {spec.key: spec.patterns for spec in ASSETS} | {pair: (pair,) for pair in DXY_WEIGHTS}
    for key, patterns in wanted.items():
        name = pick_symbol(symbols, patterns)
        if name is not None:
            found[key] = name
    return found | dict(overrides or {})


def dollar_index(quotes: Mapping[str, float]) -> tuple[float | None, int]:
    """ICE DXY from its six pairs, or a partial index from those the broker has (EURUSD at
    least, the biggest weight); the second value is how many pairs went in. A partial index
    is not comparable in level to the real DXY, only in its moves."""
    pairs = {pair: quotes[pair] for pair in DXY_WEIGHTS if quotes.get(pair, 0.0) > 0}
    if "EURUSD" not in pairs:
        return None, 0
    value = DXY_CONSTANT
    for pair, price in pairs.items():
        value *= price ** DXY_WEIGHTS[pair]
    return value, len(pairs)


# ------------------------------------------------------------------ series


class MinuteSeries:
    """The last value seen in each minute, a few days long."""

    def __init__(self, days: int) -> None:
        self.points: deque[tuple[datetime, float]] = deque(maxlen=days * 24 * 60)

    def update(self, ts: datetime, value: float) -> None:
        if not math.isfinite(value) or value <= 0:
            return
        minute = ts.replace(second=0, microsecond=0)
        if self.points and self.points[-1][0] == minute:
            self.points[-1] = (minute, value)
        elif not self.points or minute > self.points[-1][0]:
            self.points.append((minute, value))

    def seed(self, points: Iterable[tuple[datetime, float]]) -> None:
        """History from the broker, before live quotes: only points older than what we have."""
        first = self.points[0][0] if self.points else None
        older = sorted(p for p in points if first is None or p[0] < first)
        merged = [*older, *self.points]
        self.points.clear()
        for ts, value in merged:
            self.update(ts, value)

    @property
    def latest(self) -> float | None:
        return self.points[-1][1] if self.points else None

    def value_at(self, moment: datetime) -> float | None:
        for ts, value in reversed(self.points):
            if ts <= moment:
                return value
        return None

    def change_pct(self, now: datetime, minutes: int) -> float | None:
        current, previous = self.value_at(now), self.value_at(now - timedelta(minutes=minutes))
        if current is None or previous is None or previous == current == 0:
            return None
        return 100 * (current / previous - 1)

    def zscore(self, now: datetime, minutes: int, min_samples: int = 60) -> float | None:
        """This move against every move of the same length in the series."""
        current, previous = self.value_at(now), self.value_at(now - timedelta(minutes=minutes))
        if current is None or previous is None:
            return None
        points = list(self.points)
        changes = [
            math.log(points[i][1] / points[i - minutes][1])
            for i in range(minutes, len(points))
            if points[i][0] - points[i - minutes][0] <= timedelta(minutes=minutes * 2)
        ]
        if len(changes) < min_samples:
            return None
        sigma = pstdev(changes)
        if sigma == 0:
            return 0.0
        return (math.log(current / previous) - fmean(changes)) / sigma

    def buckets(self, step_minutes: int) -> dict[datetime, float]:
        """Last value per `step_minutes` bucket."""
        out: dict[datetime, float] = {}
        for ts, value in self.points:
            out[ts - timedelta(minutes=ts.minute % step_minutes)] = value
        return out


def correlation(a: MinuteSeries, b: MinuteSeries, step_minutes: int) -> tuple[float | None, int]:
    """Pearson correlation of `step_minutes` log returns over the buckets both series have."""
    left, right = a.buckets(step_minutes), b.buckets(step_minutes)
    times = sorted(set(left) & set(right))
    step = timedelta(minutes=step_minutes)
    xs: list[float] = []
    ys: list[float] = []
    for previous, current in pairwise(times):
        if current - previous != step:
            continue
        xs.append(math.log(left[current] / left[previous]))
        ys.append(math.log(right[current] / right[previous]))
    if len(xs) < CORR_MIN_PAIRS:
        return None, len(xs)
    sx, sy = pstdev(xs), pstdev(ys)
    if sx == 0 or sy == 0:
        return None, len(xs)
    mx, my = fmean(xs), fmean(ys)
    cov = fmean((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return cov / (sx * sy), len(xs)


def relevance(spec: AssetSpec, measured: float | None, pairs: int) -> tuple[int, float, float]:
    """(direction, weight 0-1, correlation used): the measured correlation shrunk towards the
    usual one. An expected relationship that has broken (or reversed) counts for nothing; an
    asset without a stable rule follows the sign the data shows."""
    observed = pairs if measured is not None else 0
    corr = (observed * (measured or 0.0) + CORR_PRIOR_PAIRS * spec.prior_corr) / (
        observed + CORR_PRIOR_PAIRS
    )
    sign = spec.sign or (1 if corr > 0 else -1)
    return sign, max(0.0, min(1.0, sign * corr / CORR_FULL)), corr


def combined_z(z_15m: float | None, z_1h: float | None) -> float | None:
    if z_15m is None and z_1h is None:
        return None
    if z_15m is None or z_1h is None:
        return z_1h if z_1h is not None else z_15m
    return 0.6 * z_1h + 0.4 * z_15m


class CrossTracker:
    """Every related market's minute series plus gold's, and their snapshot for the context."""

    def __init__(self, days: int) -> None:
        self.days = days
        self.gold = MinuteSeries(days)
        self.series: dict[str, MinuteSeries] = {}
        self.symbols: dict[str, str] = {}  # logical -> broker symbol (or "sintetis 3/6")
        self.basket: set[str] = set()  # the DXY pairs this broker has (fixed once resolved)
        self.inverted: set[str] = set()  # bond prices turned into a yield-like series
        self._corr: dict[str, tuple[float | None, int]] = {}
        self._corr_minute: datetime | None = None

    def series_for(self, key: str) -> MinuteSeries:
        if key not in self.series:
            self.series[key] = MinuteSeries(self.days)
        return self.series[key]

    def update_gold(self, ts: datetime, mid: float) -> None:
        self.gold.update(ts, mid)

    def _values(self, quotes: Mapping[str, float]) -> dict[str, float]:
        """Logical quotes as tracked: the dollar index from its pairs unless the broker quotes
        DXY itself, and a bond price as 100/price so it moves like the yield."""
        values: dict[str, float] = {}
        for spec in ASSETS:
            price = quotes.get(spec.key, 0.0)
            if price <= 0:
                continue
            if spec.key == "US10Y" and price > YIELD_MAX:
                self.inverted.add(spec.key)
                price = 100.0 / price
            values[spec.key] = price
        # Always the same pairs, or the partial index's level would jump when one is missing.
        if "DXY" not in values and self.basket and self.basket <= set(quotes):
            dxy, used = dollar_index({pair: quotes[pair] for pair in self.basket})
            if dxy is not None:
                values["DXY"] = dxy
                self.symbols["DXY"] = f"sintetis {used}/{len(DXY_WEIGHTS)} pasangan"
        return values

    def update(self, ts: datetime, quotes: Mapping[str, float]) -> float | None:
        """New quotes (logical name -> mid). Returns the dollar index, if any."""
        values = self._values(quotes)
        for key, value in values.items():
            self.series_for(key).update(ts, value)
        return values.get("DXY")

    def seed(self, history: Mapping[str, Sequence[tuple[datetime, float]]]) -> None:
        """Broker history (logical name -> minute closes) so z-scores and correlations work
        from the first minute. The dollar index is rebuilt minute by minute from the same pairs
        the live quotes use, so its level never jumps between history and live."""
        by_minute: dict[datetime, dict[str, float]] = {}
        for key, points in history.items():
            for ts, value in points:
                by_minute.setdefault(ts, {})[key] = value
        merged: dict[str, list[tuple[datetime, float]]] = {}
        for ts in sorted(by_minute):
            for key, value in self._values(by_minute[ts]).items():
                merged.setdefault(key, []).append((ts, value))
        for key, points in merged.items():
            self.series_for(key).seed(points)

    def _correlations(self, now: datetime) -> dict[str, tuple[float | None, int]]:
        minute = now.replace(second=0, microsecond=0)
        if minute != self._corr_minute:
            self._corr_minute = minute
            self._corr = {
                key: correlation(series, self.gold, CORR_STEP_MINUTES)
                for key, series in self.series.items()
            }
        return self._corr

    def snapshot(self, now: datetime) -> dict[str, CrossAsset]:
        out: dict[str, CrossAsset] = {}
        correlations = self._correlations(now)
        for spec in ASSETS:
            series = self.series.get(spec.key)
            if series is None or series.latest is None:
                continue
            measured, pairs = correlations.get(spec.key, (None, 0))
            sign, weight, corr = relevance(spec, measured, pairs)
            z_15m, z_1h = series.zscore(now, 15), series.zscore(now, 60)
            z = combined_z(z_15m, z_1h)
            effect = None if z is None else sign * weight * math.tanh(z / 2)
            out[spec.key] = CrossAsset(
                key=spec.key,
                label=spec.label,
                symbol=self.symbols.get(spec.key, spec.key),
                note="harga obligasi, dibalik agar searah yield"
                if spec.key in self.inverted
                else None,
                value=round(series.latest, 6),
                change_15m_pct=_round(series.change_pct(now, 15), 3),
                change_1h_pct=_round(series.change_pct(now, 60), 3),
                change_1d_pct=_round(series.change_pct(now, 24 * 60), 3),
                change_5d_pct=_round(series.change_pct(now, 5 * 24 * 60), 3),
                z_15m=_round(z_15m, 2),
                z_1h=_round(z_1h, 2),
                corr_measured=_round(measured, 3),
                corr=round(corr, 3),
                pairs=pairs,
                relevance=round(weight, 3),
                effect=_round(effect, 4),
                weight=spec.weight,
                why=spec.why,
            )
        return out


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)

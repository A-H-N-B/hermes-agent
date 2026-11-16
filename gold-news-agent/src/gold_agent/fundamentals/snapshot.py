"""The day's fundamental bias for gold from the slow drivers, each explained in one line.

Every driver is turned into a pull on gold in [-1, 1] with a fixed scale (a typical notable
move gives about ±0.75) and a prior weight; the bias is the weighted sum over all drivers, a
driver without data counting as neutral, so a minor driver cannot speak for missing major ones.
These scales and weights are priors from the literature on gold, not fitted values: the Performa
page and `gold-agent calibrate` measure whether the macro score earns its weight.

- US real 10-year yield (TIPS): the opportunity cost of holding gold; 15 bp in 5 days is notable.
- US 2-year yield: where the market sees the Fed; rising = hawkish.
- 10-year breakeven (nominal minus real): inflation expectations; gold is an inflation hedge.
- The dollar over 5 days (from the broker's FX pairs).
- CFTC managed money: crowded longs (top 15% of 3 years) warn of a shake-out, crowded shorts
  of a squeeze; in between, the weekly change shows which way speculators are moving.
- GLD holdings over 20 sessions: Western investment flows.
- US data surprises over 30 days (our own release parser, half-life 7 days).
- Geopolitical news in the last 24 h against the previous fortnight's daily average.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from gold_agent.fundamentals.parsers import CotWeek
from gold_agent.models import Analysis, Factor, FundamentalSnapshot, ReleaseReading

KV_FUNDAMENTALS = "fundamentals"  # the latest FundamentalSnapshot (dashboard, AI brief)
CURVE_LOOKBACK = 5  # sessions
COT_WEEKS = 156  # three years
COT_CROWDED = 0.85
GLD_LOOKBACK = 20
SURPRISE_DAYS = 30
SURPRISE_HALF_LIFE_DAYS = 7
GEO_BASELINE_DAYS = 14

WEIGHTS: dict[str, float] = {
    "real10y": 0.25,
    "us2y": 0.20,
    "breakeven": 0.10,
    "dollar": 0.15,
    "cot": 0.10,
    "gld": 0.10,
    "surprise": 0.07,
    "geopolitics": 0.03,
}
LABELS: dict[str, str] = {
    "real10y": "Yield riil AS 10 th (TIPS)",
    "us2y": "Yield AS 2 th (arah suku bunga Fed)",
    "breakeven": "Ekspektasi inflasi 10 th (breakeven)",
    "dollar": "Dolar AS 5 hari",
    "cot": "Posisi spekulan (CFTC, managed money)",
    "gld": "Arus dana ETF emas (GLD)",
    "surprise": "Kejutan data ekonomi AS (30 hari)",
    "geopolitics": "Intensitas berita geopolitik",
}


@dataclass
class Inputs:
    nominal: dict[date, dict[str, float]] = field(default_factory=lambda: {})
    real: dict[date, dict[str, float]] = field(default_factory=lambda: {})
    cot: list[CotWeek] = field(default_factory=lambda: list[CotWeek]())
    gld: list[tuple[date, float]] = field(default_factory=lambda: list[tuple[date, float]]())
    releases: list[ReleaseReading] = field(default_factory=lambda: list[ReleaseReading]())
    analyses: list[Analysis] = field(default_factory=lambda: list[Analysis]())
    dollar_5d_pct: float | None = None
    dollar_source: str | None = None


def series(curve: dict[date, dict[str, float]], key: str) -> list[tuple[date, float]]:
    return [(day, values[key]) for day, values in sorted(curve.items()) if key in values]


def change(points: Sequence[tuple[date, float]], back: int) -> tuple[float, float, date] | None:
    """(latest, change over `back` observations, latest day), or None with fewer than two."""
    if len(points) < 2:
        return None
    then = points[max(0, len(points) - 1 - back)][1]
    day, latest = points[-1]
    return latest, latest - then, day


def _direction(effect: float, up: str, down: str) -> str:
    if effect >= 0.15:
        return up
    if effect <= -0.15:
        return down
    return "pengaruhnya kecil saat ini"


def _yield_factor(key: str, points: Sequence[tuple[date, float]], scale_bp: float) -> Factor | None:
    moved = change(points, CURVE_LOOKBACK)
    if moved is None:
        return None
    latest, delta, day = moved
    bp = delta * 100
    sign = 1 if key == "breakeven" else -1
    effect = sign * math.tanh(bp / scale_bp)
    if key == "breakeven":
        note = _direction(effect, "inflasi diperkirakan naik → mendukung emas", "turun → menekan")
    else:
        note = _direction(effect, "turun → mendukung emas", "naik → menekan emas")
    return Factor(
        key=key,
        label=LABELS[key],
        value=f"{latest:.2f}%",
        change=f"{bp:+.0f} bp dalam {CURVE_LOOKBACK} hari bursa",
        effect=round(effect, 3),
        weight=WEIGHTS[key],
        note=note,
        as_of=day.isoformat(),
        source="US Treasury",
    )


def yield_factors(inputs: Inputs) -> list[Factor]:
    real = series(inputs.real, "10Y")
    nominal10 = dict(series(inputs.nominal, "10Y"))
    breakeven = [(day, nominal10[day] - value) for day, value in real if day in nominal10]
    found = [
        _yield_factor("real10y", real, 15),
        _yield_factor("us2y", series(inputs.nominal, "2Y"), 15),
        _yield_factor("breakeven", breakeven, 8),
    ]
    return [f for f in found if f is not None]


def dollar_factor(inputs: Inputs) -> Factor | None:
    if inputs.dollar_5d_pct is None:
        return None
    pct = inputs.dollar_5d_pct
    effect = -math.tanh(pct / 1.0)
    return Factor(
        key="dollar",
        label=LABELS["dollar"],
        value=f"{pct:+.2f}%",
        change="5 hari",
        effect=round(effect, 3),
        weight=WEIGHTS["dollar"],
        note=_direction(effect, "dolar melemah → mendukung emas", "dolar menguat → menekan emas"),
        source=inputs.dollar_source or "MT5",
    )


def cot_factor(weeks: Sequence[CotWeek]) -> Factor | None:
    if len(weeks) < 2:
        return None
    window = list(weeks[-COT_WEEKS:])
    latest, previous = window[-1], window[-2]
    rank = sum(w.net_share <= latest.net_share for w in window) / len(window)
    if rank >= COT_CROWDED:
        effect, note = -0.5, f"spekulan sangat long (persentil {rank:.0%} 3 th): rawan aksi jual"
    elif rank <= 1 - COT_CROWDED:
        effect, note = 0.5, f"spekulan sangat short (persentil {rank:.0%}): rawan short squeeze"
    else:
        effect = 0.3 * math.tanh((latest.net_share - previous.net_share) / 0.03)
        note = f"persentil {rank:.0%} dari 3 th; " + _direction(
            effect, "spekulan menambah long", "spekulan mengurangi long"
        )
    return Factor(
        key="cot",
        label=LABELS["cot"],
        value=f"net {latest.net / 1000:+.0f} rb kontrak ({latest.net_share:.0%} OI)",
        change=f"{(latest.net - previous.net) / 1000:+.1f} rb vs pekan lalu",
        effect=round(effect, 3),
        weight=WEIGHTS["cot"],
        note=note,
        as_of=latest.day.isoformat(),
        source="CFTC",
    )


def gld_factor(points: Sequence[tuple[date, float]]) -> Factor | None:
    month = change(points, GLD_LOOKBACK)
    week = change(points, 5)
    if month is None or week is None:
        return None
    tonnes, delta20, day = month
    effect = math.tanh(delta20 / 20)
    return Factor(
        key="gld",
        label=LABELS["gld"],
        value=f"{tonnes:.1f} ton",
        change=f"{week[1]:+.1f} t (5 hari), {delta20:+.1f} t (20 hari)",
        effect=round(effect, 3),
        weight=WEIGHTS["gld"],
        note=_direction(effect, "dana masuk ke ETF emas", "dana keluar dari ETF emas"),
        as_of=day.isoformat(),
        source="SPDR Gold Shares",
    )


def surprise_factor(releases: Sequence[ReleaseReading], now: datetime) -> Factor | None:
    """Decayed sum of hawkish surprises: US data beating forecasts lifts the dollar and yields."""
    recent = [
        r
        for r in releases
        if r.hawkish_z is not None and now - r.first_seen_at <= timedelta(days=SURPRISE_DAYS)
    ]
    if not recent:
        return None
    index = sum(
        (r.hawkish_z or 0.0)
        * 0.5 ** ((now - r.first_seen_at).total_seconds() / 86400 / SURPRISE_HALF_LIFE_DAYS)
        for r in recent
    )
    effect = -math.tanh(index / 2)
    return Factor(
        key="surprise",
        label=LABELS["surprise"],
        value=f"{index:+.2f}",
        change=f"{len(recent)} rilis",
        effect=round(effect, 3),
        weight=WEIGHTS["surprise"],
        note=_direction(
            effect,
            "data AS lebih lemah dari perkiraan → mendukung emas",
            "data AS lebih kuat dari perkiraan → menekan emas",
        ),
        as_of=max(r.first_seen_at for r in recent).date().isoformat(),
        source="rilis data (parser sistem)",
    )


def geopolitics_factor(analyses: Sequence[Analysis], now: datetime) -> Factor | None:
    if not analyses:
        return None

    def heat(start: datetime, end: datetime) -> float:
        return sum(
            a.triage.impact_score / 5
            for a in analyses
            if a.triage.category == "geopolitics"
            and a.triage.is_gold_relevant
            and start <= a.first_seen_at < end
        )

    day_ago = now - timedelta(days=1)
    last_day = heat(day_ago, now)
    baseline = heat(day_ago - timedelta(days=GEO_BASELINE_DAYS), day_ago) / GEO_BASELINE_DAYS
    ratio = last_day / baseline if baseline > 0 else (2.0 if last_day > 0 else 1.0)
    effect = 0.8 * math.tanh(max(0.0, ratio - 1) / 2)  # tension supports gold; calm is neutral
    return Factor(
        key="geopolitics",
        label=LABELS["geopolitics"],
        value=f"{last_day:.1f} (bobot dampak, 24 jam)",
        change=f"{ratio:.1f}× rata-rata {GEO_BASELINE_DAYS} hari",
        effect=round(effect, 3),
        weight=WEIGHTS["geopolitics"],
        note=_direction(effect, "ketegangan meningkat → permintaan aset aman", ""),
        source="berita (analisis sistem)",
    )


def build_snapshot(inputs: Inputs, now: datetime) -> FundamentalSnapshot:
    found = [
        *yield_factors(inputs),
        dollar_factor(inputs),
        cot_factor(inputs.cot),
        gld_factor(inputs.gld),
        surprise_factor(inputs.releases, now),
        geopolitics_factor(inputs.analyses, now),
    ]
    factors = [f for f in found if f is not None]
    have = {f.key for f in factors}
    missing = [LABELS[key] for key in WEIGHTS if key not in have]
    if not factors:
        return FundamentalSnapshot(
            built_at=now, summary="data fundamental belum tersedia", missing=missing
        )
    bias = sum(f.weight * f.effect for f in factors) / sum(WEIGHTS.values())
    leaders = sorted(factors, key=lambda f: abs(f.weight * f.effect), reverse=True)[:2]
    word = "NAIK" if bias >= 0.1 else "TURUN" if bias <= -0.1 else "NETRAL"
    reasons = "; ".join(f"{f.label}: {f.note}" for f in leaders if abs(f.effect) >= 0.15)
    return FundamentalSnapshot(
        built_at=now,
        factors=factors,
        bias=round(bias, 3),
        summary=f"Fundamental harian condong {word} ({bias:+.2f})"
        + (f" — {reasons}" if reasons else ""),
        missing=missing,
    )

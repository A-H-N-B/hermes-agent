"""Composite score (section 10.1–10.2). Every component lives in [-1, 1]; positive = bullish gold.

N  news:   tanh( Σ dir × impact/5 × confidence × reliability × weight × decay )
M  macro:  the markets that move gold, read live from the broker (dollar, US yields, USDJPY,
           silver, oil, S&P 500, VIX: each its 15m/1h move as a z-score, counting as much as
           its measured link with gold supports, market/cross_assets.py), the daily yields,
           and the day's fundamental bias (fundamentals/); weighted mean of what is available
T  trend:  H1/H4 EMA trend, RSI regime, price vs session VWAP
P  price:  move since the dominant news (or recent momentum), in ATR(M15) units
S = (wN·N + wM·M + wT·T + wP·P) × session liquidity factor
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from gold_agent.config import Weights
from gold_agent.models import Analysis, MarketContext, SignalComponents
from gold_agent.models.common import DIRECTION_SIGN

PRICED_IN_FACTOR = {"low": 1.0, "medium": 0.8, "high": 0.5}
STALE_NEWS_HALF_LIVES = 4.0  # contributions beyond 4 half-lives (~6%) are dropped

PriceAt = Callable[[datetime], float | None]


@dataclass(frozen=True)
class NewsContribution:
    analysis: Analysis
    value: float  # signed, before tanh


@dataclass
class ScoreBreakdown:
    components: SignalComponents
    composite: float
    composite_confirmed_only: float
    contributions: list[NewsContribution] = field(default_factory=lambda: list[NewsContribution]())
    macro_available: bool = False
    macro_parts: list[tuple[str, float]] = field(
        default_factory=lambda: list[tuple[str, float]]()
    )  # (label, signed contribution to M), strongest first
    dominant: NewsContribution | None = None
    dominant_confirmed: NewsContribution | None = None  # strongest story not needing confirmation
    n_confirmed: float = 0.0
    price_move: float | None = None  # $ since the dominant news (or momentum window)
    notes: list[str] = field(default_factory=lambda: list[str]())


def _clip(value: float, bound: float = 1.0) -> float:
    return max(-bound, min(bound, value))


def news_contributions(
    analyses: Sequence[Analysis],
    now: datetime,
    *,
    reliability: Callable[[Analysis], float] | None = None,
    confirmed_only: bool = False,
) -> list[NewsContribution]:
    result: list[NewsContribution] = []
    for analysis in analyses:
        triage = analysis.triage
        sign = DIRECTION_SIGN.get(triage.direction_gold, 0)
        if not triage.is_gold_relevant or sign == 0:
            continue
        if confirmed_only and triage.needs_confirmation:
            continue
        age = (now - analysis.first_seen_at).total_seconds() / 60
        if age < 0 or age > STALE_NEWS_HALF_LIVES * analysis.half_life_minutes:
            continue
        decay = math.exp(-math.log(2) * age / analysis.half_life_minutes)
        source_reliability = reliability(analysis) if reliability else analysis.reliability
        novelty = 1.0 if triage.is_new_information else 0.5
        value = (
            sign
            * (triage.impact_score / 5)
            * triage.confidence
            * source_reliability
            * analysis.weight
            * PRICED_IN_FACTOR.get(triage.priced_in_risk, 1.0)
            * novelty
            * decay
        )
        if value:
            result.append(NewsContribution(analysis, value))
    return result


USD_WEIGHT = 0.40  # the dollar when only a USD proxy is available (no broker markets)
DAILY_YIELD_WEIGHT = 0.15  # yesterday's 10-year yield change, when no daily bias exists
FUNDAMENTAL_WEIGHT = 0.15  # the day's fundamental bias next to the live markets (sum 1.0)


def macro_parts(context: MarketContext) -> list[tuple[str, float, float]]:
    """(label, weight, pull on gold -1..1) of every macro input this context has."""
    parts: list[tuple[str, float, float]] = []
    for asset in context.cross.values():
        if asset.effect is not None:
            parts.append((asset.label, asset.weight, asset.effect))
    if "DXY" not in context.cross and (
        context.dxy_z_1h is not None or context.dxy_z_15m is not None
    ):
        z = 0.6 * (context.dxy_z_1h or 0.0) + 0.4 * (context.dxy_z_15m or 0.0)
        parts.append((f"USD ({context.dxy_source or 'proxy'})", USD_WEIGHT, -math.tanh(z / 2)))
    if context.fundamental_bias is not None:
        parts.append(("fundamental harian", FUNDAMENTAL_WEIGHT, context.fundamental_bias))
    else:
        change = context.real_yield_change_1d
        if change is None:
            change = context.us10y_change_1d
        if change is not None:  # 5 bp/day is a notable move
            parts.append(("yield AS 10 th (harian)", DAILY_YIELD_WEIGHT, -math.tanh(change / 0.05)))
    return parts


def macro_component(context: MarketContext) -> tuple[float, bool]:
    parts = macro_parts(context)
    if not parts:
        return 0.0, False
    total = sum(weight for _, weight, _ in parts)
    return _clip(sum(weight * pull for _, weight, pull in parts) / total), True


def technical_component(context: MarketContext) -> float:
    trend_value = {"up": 1.0, "down": -1.0, "flat": 0.0}
    h1 = context.timeframes.get("H1")
    h4 = context.timeframes.get("H4")
    m15 = context.timeframes.get("M15")
    trend = 0.4 * trend_value.get(h1.trend or "", 0.0) if h1 else 0.0
    trend += 0.6 * trend_value.get(h4.trend or "", 0.0) if h4 else 0.0
    rsi = _clip(((h1.rsi14 or 50.0) - 50.0) / 20.0) if h1 else 0.0
    vwap = 0.0
    atr = m15.atr14 if m15 else None
    if context.vwap is not None and context.mid is not None and atr:
        vwap = _clip((context.mid - context.vwap) / atr)
    return _clip(0.5 * trend + 0.25 * rsi + 0.25 * vwap)


def price_component(
    context: MarketContext, dominant: NewsContribution | None, price_at: PriceAt, now: datetime
) -> tuple[float, float | None]:
    m15 = context.timeframes.get("M15")
    atr = m15.atr14 if m15 else None
    if context.mid is None or not atr:
        return 0.0, None
    if dominant is not None:
        then = price_at(dominant.analysis.first_seen_at)
        if then is not None:
            move = context.mid - then
            return math.tanh(move / atr), move
    then = price_at(now - timedelta(minutes=45))
    if then is None:
        return 0.0, None
    move = context.mid - then
    return 0.5 * math.tanh(move / atr), move  # momentum without a news anchor counts half


def score(
    analyses: Sequence[Analysis],
    context: MarketContext,
    *,
    weights: Weights,
    now: datetime,
    price_at: PriceAt,
    reliability: Callable[[Analysis], float] | None = None,
) -> ScoreBreakdown:
    contributions = news_contributions(analyses, now, reliability=reliability)
    confirmed = news_contributions(analyses, now, reliability=reliability, confirmed_only=True)
    n = math.tanh(sum(c.value for c in contributions))
    n_confirmed = math.tanh(sum(c.value for c in confirmed))
    dominant = max(contributions, key=lambda c: abs(c.value), default=None)
    dominant_confirmed = max(confirmed, key=lambda c: abs(c.value), default=None)
    m, macro_ok = macro_component(context)
    t = technical_component(context)
    p, move = price_component(context, dominant, price_at, now)
    # Without unconfirmed stories the price check is anchored on confirmed news only, so a
    # single-source claim can neither add news weight nor lend its move to the score (12).
    p_confirmed, _ = price_component(context, dominant_confirmed, price_at, now)
    factor = context.liquidity_factor if context.market_open else 0.0
    base = weights.macro * m + weights.technical * t
    composite = (weights.news * n + base + weights.price_confirmation * p) * factor
    composite_confirmed = (
        weights.news * n_confirmed + base + weights.price_confirmation * p_confirmed
    ) * factor
    notes: list[str] = []
    if not macro_ok:
        notes.append("komponen makro tidak tersedia (USD/yield) → M = 0")
    parts = macro_parts(context)
    total = sum(weight for _, weight, _ in parts) or 1.0
    macro_pulls = sorted(
        ((label, round(weight * pull / total, 4)) for label, weight, pull in parts),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    return ScoreBreakdown(
        components=SignalComponents(
            N=round(n, 4), M=round(m, 4), T=round(t, 4), P=round(p, 4), session_factor=factor
        ),
        composite=round(composite, 4),
        composite_confirmed_only=round(composite_confirmed, 4),
        contributions=contributions,
        macro_available=macro_ok,
        macro_parts=macro_pulls,
        dominant=dominant,
        dominant_confirmed=dominant_confirmed,
        n_confirmed=round(n_confirmed, 4),
        price_move=move,
        notes=notes,
    )


def confidence(breakdown: ScoreBreakdown, context: MarketContext, weights: Weights) -> int:
    """0–100: strength of |S|, agreement between components, minus data-quality penalties."""
    s = breakdown.composite
    if s == 0 or context.stale:
        return 0
    c = breakdown.components
    weighted = [
        (weights.news, c.N),
        (weights.macro, c.M),
        (weights.technical, c.T),
        (weights.price_confirmation, c.P),
    ]
    active = [(w, v) for w, v in weighted if abs(v) > 0.1]
    agree = sum(w for w, v in active if (v > 0) == (s > 0))
    agreement = agree / sum(w for w, _ in active) if active else 0.0
    quality = 1.0
    if context.proxy:
        quality *= 0.9
    if not breakdown.macro_available:
        quality *= 0.9
    dominant = breakdown.dominant
    if dominant is not None and dominant.analysis.ai_pending:
        quality *= 0.85
    if dominant is not None and dominant.analysis.triage.needs_confirmation:
        quality *= 0.8
    strength = min(1.0, abs(s) / 0.6)
    return round(100 * strength * (0.5 + 0.5 * agreement) * quality)

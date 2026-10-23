"""Rule-based triage: the always-available baseline and the fallback when the LLM is unavailable.

Deliberately conservative: low confidence, and summaries say the AI analysis is pending.
"""

from __future__ import annotations

import re
from typing import Literal

from gold_agent.models import (
    NewsCluster,
    PrefilterResult,
    ReleaseReading,
    SurpriseInfo,
    TriageResult,
)
from gold_agent.models.common import Category, Direction, EventType

# (lexicon key, direction for gold, pattern). First match wins, so de-escalation and
# "sell" patterns come before the broader escalation/buy ones.
_LEXICON_TABLE: list[tuple[str, Direction, str]] = [
    ("fed_policy", "bearish", r"hawkish|rate hike|hikes? rates|higher for longer"),
    ("fed_policy", "bearish", r"fewer (rate )?cuts|tighten"),
    ("fed_policy", "bullish", r"dovish|rate cuts?|cuts? rates|easing|more cuts|pause hikes"),
    ("geopolitics", "bearish", r"ceasefire|truce|peace (deal|talks)|de-?escalat|withdraw"),
    ("geopolitics", "bullish", r"attack|strike|missile|invasion|war\b|escalat|explosion"),
    ("geopolitics", "bullish", r"nuclear|sanction"),
    ("usd", "bearish", r"dollar (rises|gains|jumps|climbs|strengthens|rallies|surges)"),
    ("usd", "bearish", r"dxy (up|rises|higher)"),
    ("usd", "bullish", r"dollar (falls|slides|drops|weakens|slumps|declines)"),
    ("usd", "bullish", r"dxy (down|falls|lower)"),
    ("yields", "bearish", r"yields? (rises?|jumps?|climbs?|surges?|spikes?|higher)"),
    ("yields", "bullish", r"yields? (falls?|drops?|slides?|declines?|lower)"),
    ("central_bank_gold", "bearish", r"\b(sell|sells|selling|sold)\b"),
    ("central_bank_gold", "bullish", r"\b(buy|buys|buying|bought|adds?|added|purchas)\w*"),
    ("trade_tariff", "bearish", r"trade deal|tariff (cut|pause|relief)|truce|rollback"),
    ("trade_tariff", "bullish", r"tariffs?|trade war|retaliat|export (ban|controls)"),
    ("fiscal_debt", "bullish", r"shutdown|downgrade|default|debt ceiling|deficit"),
    ("gold", "bearish", r"gold (falls|slides|drops|slumps|declines|retreats|tumbles)"),
    ("gold", "bullish", r"gold (rises|gains|jumps|climbs|rallies|surges|hits record)"),
    ("gold", "bullish", r"gold .{0,20}record high"),
]
_LEXICON: dict[str, list[tuple[re.Pattern[str], Direction]]] = {}
for _key, _direction_value, _pattern in _LEXICON_TABLE:
    _LEXICON.setdefault(_key, []).append((re.compile(_pattern, re.I), _direction_value))

_BASE_IMPACT: dict[str, int] = {
    "fed_policy": 3,
    "us_inflation": 3,
    "us_labor": 3,
    "us_growth": 3,
    "geopolitics": 3,
    "trade_tariff": 3,
    "central_bank_gold": 2,
    "fiscal_debt": 2,
    "usd": 2,
    "yields": 2,
    "risk_sentiment": 2,
    "etf_flows": 2,
    "commentary": 1,
    "other": 1,
}
_RELEASE_CATEGORY: dict[str, Category] = {
    "cpi": "us_inflation",
    "pce": "us_inflation",
    "ppi": "us_inflation",
    "nfp": "us_labor",
    "adp": "us_labor",
    "unemployment": "us_labor",
    "claims": "us_labor",
    "earnings": "us_labor",
    "jolts": "us_labor",
    "retail": "us_growth",
    "ism": "us_growth",
    "gdp": "us_growth",
}
_INFLATION = re.compile(r"\b(cpi|pce|ppi|inflation|inflasi)\b", re.I)
_LABOR = re.compile(r"payrolls|nfp|jobless|unemployment|jobs report|employment", re.I)
_SPEECH = re.compile(r"\b(says|said|speech|remarks|testimony|interview|warns|comments?)\b", re.I)
_RUMOR = re.compile(r"\b(rumou?r|reportedly|sources say|unconfirmed|said to|considering)\b", re.I)
_SUDDEN_POLICY = re.compile(
    r"\b(emergency|unscheduled|intermeeting|inter-meeting|surprise cut|surprise hike)\b", re.I
)


_GROUP_CATEGORY: list[tuple[str, Category]] = [
    ("fed", "fed_policy"),
    ("central_bank_gold", "central_bank_gold"),
    ("geopolitics", "geopolitics"),
    ("geo_actors", "geopolitics"),
]


def _category(prefilter: PrefilterResult, text: str) -> tuple[Category, str]:
    """(category, lexicon key) from the prefilter groups that matched."""
    groups = prefilter.categories
    if "us_data" in groups:
        if _INFLATION.search(text):
            return "us_inflation", "us_data"
        if _LABOR.search(text):
            return "us_labor", "us_data"
        return "us_growth", "us_data"
    for group, category in _GROUP_CATEGORY:
        if group in groups:
            return category, category
    if "usd" in groups:
        return "usd", "usd"
    if "trade_fiscal" in groups:
        if re.search(r"tariff|trade|tarif", text, re.I):
            return "trade_tariff", "trade_tariff"
        return "fiscal_debt", "fiscal_debt"
    if "usd_rates" in groups:
        if re.search(r"yield|treasur|tips", text, re.I):
            return "yields", "yields"
        return "usd", "usd"
    if "risk" in groups:
        return "risk_sentiment", "risk_sentiment"
    if "gold" in groups:
        return "commentary", "gold"
    return "other", "other"


def _direction(lexicon_key: str, text: str) -> Direction:
    for pattern, direction in _LEXICON.get(lexicon_key, []):
        if pattern.search(text):
            return direction
    return "neutral"


def release_triage(release: ReleaseReading, item_ids: list[str]) -> TriageResult:
    category: Category = "us_growth"
    for marker, candidate in _RELEASE_CATEGORY.items():
        if marker in release.event_key:
            category = candidate
            break
    strength = {"strong": 5, "mild": 4}.get(release.bucket.split("_")[0], 2)
    confidence = 0.75 if release.z_basis == "calibrated" else 0.6
    direction_text = {"bullish": "bullish", "bearish": "bearish", "neutral": "netral"}.get(
        release.direction_gold, "netral"
    )
    forecast = "n/a" if release.forecast is None else f"{release.forecast:g}{release.unit}"
    z_text = "" if release.z is None else f" (z≈{release.z:+.1f}, {release.z_basis})"
    return TriageResult(
        is_gold_relevant=True,
        category=category,
        event_type="scheduled_release",
        is_new_information=True,
        direction_gold=release.direction_gold,
        impact_score=strength if release.forecast is not None else 3,
        confidence=confidence if release.forecast is not None else 0.4,
        expected_horizon="minutes",
        surprise=SurpriseInfo(
            actual=release.actual,
            forecast=release.forecast,
            previous=release.previous,
            unit=release.unit,
            surprise_value=release.surprise,
        ),
        priced_in_risk="low",
        needs_confirmation=False,
        entities=["US", release.event_name],
        summary_id=(
            f"Rilis {release.event_name}: aktual {release.actual:g}{release.unit} vs perkiraan "
            f"{forecast}{z_text} → {direction_text} untuk emas."
        ),
        reasoning_id=(
            "Kejutan ke arah hawkish menaikkan ekspektasi suku bunga (bearish emas); "
            "kejutan dovish sebaliknya. Arah dari parser angka, bukan LLM."
            if release.direction_gold != "neutral"
            else "Angka dekat ekspektasi; reaksi bergantung pada komponen inti dan revisi."
        ),
        evidence=[release.headline],
        source_item_ids=item_ids,
    )


def rules_triage(
    cluster: NewsCluster,
    prefilter: PrefilterResult,
    *,
    big_claim_categories: tuple[str, ...],
    fed_blackout: bool = False,
) -> TriageResult:
    text = f"{cluster.title} {cluster.body or ''}"
    category, lexicon_key = _category(prefilter, text)
    direction = _direction(lexicon_key, text)
    event_type: EventType = "breaking"
    if _RUMOR.search(text):
        event_type = "rumor"
    elif category == "commentary":
        event_type = "recap" if direction != "neutral" else "analysis"
    elif _SPEECH.search(text):
        event_type = "speech"
    impact = _BASE_IMPACT.get(category, 1)
    if cluster.tier >= 3:
        impact -= 1
    if cluster.has_official:
        impact += 1
    if event_type in ("recap", "analysis"):
        impact = min(impact, 1)
    impact = max(0, min(5, impact if prefilter.relevant else 0))
    single_unofficial = not cluster.has_official and cluster.confirmation_count == 0
    needs_confirmation = (
        event_type == "rumor"
        or (category in big_claim_categories and single_unofficial and category != "fed_policy")
        or (category == "fed_policy" and event_type == "speech" and fed_blackout)
        or (category == "fed_policy" and bool(_SUDDEN_POLICY.search(text)) and single_unofficial)
    )
    confidence = 0.35 if direction != "neutral" else 0.25
    horizon: Literal["minutes", "hours", "days", "weeks"] = (
        "days" if category in ("central_bank_gold", "fiscal_debt", "etf_flows") else "hours"
    )
    terms = ", ".join(prefilter.matched_terms[:5]) or "-"
    blackout = (
        " Periode blackout Fed: klaim pejabat Fed perlu dicek."
        if fed_blackout and category == "fed_policy"
        else ""
    )
    return TriageResult(
        is_gold_relevant=prefilter.relevant,
        category=category,
        event_type=event_type,
        is_new_information=event_type not in ("recap", "analysis"),
        direction_gold=direction,
        impact_score=impact,
        confidence=confidence,
        expected_horizon=horizon,
        surprise=None,
        priced_in_risk="medium",
        needs_confirmation=needs_confirmation,
        entities=[],
        summary_id=f"(Analisis aturan — AI tertunda) {cluster.title[:200]}",
        reasoning_id=f"Kata kunci: {terms}. Arah dari kamus aturan kategori {category}.{blackout}",
        evidence=[cluster.title[:300]],
        source_item_ids=list(cluster.item_ids),
    )

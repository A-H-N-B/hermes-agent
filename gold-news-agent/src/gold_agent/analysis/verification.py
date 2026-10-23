"""Cross-verification (7.8) and anti-hallucination checks on LLM output."""

from __future__ import annotations

import re

from gold_agent.models import Analysis, NewsCluster, NewsItem, TriageResult

_SPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip().lower()


def verify_triage(result: TriageResult, items: list[NewsItem]) -> tuple[TriageResult, list[str]]:
    """Keep only what the source text supports: known ids, quoted evidence, numbers present."""
    notes: list[str] = []
    corpus = _norm(" ".join(f"{i.title} {i.body or ''}" for i in items))
    known_ids = {i.id for i in items}
    ids = [i for i in result.source_item_ids if i in known_ids] or sorted(known_ids)
    if len(ids) != len(result.source_item_ids):
        notes.append("source_item_ids diperbaiki ke id yang benar-benar diberikan")
    evidence = [e for e in result.evidence if _norm(e) and _norm(e) in corpus]
    confidence = result.confidence
    if result.evidence and not evidence:
        notes.append("evidence LLM tidak ditemukan di teks sumber; confidence diturunkan")
        confidence = round(confidence * 0.8, 3)
    surprise = result.surprise
    if surprise is not None:
        present = {float(n.replace(",", ".")) for n in _NUMBER.findall(corpus)}
        claimed = [
            v for v in (surprise.actual, surprise.forecast, surprise.previous) if v is not None
        ]
        if any(abs(v) not in {abs(p) for p in present} for v in claimed):
            notes.append("angka surprise dari LLM tidak ada di teks; dihapus")
            surprise = None
    verified = result.model_copy(
        update={
            "source_item_ids": ids,
            "evidence": evidence,
            "confidence": confidence,
            "surprise": surprise,
        }
    )
    return verified, notes


def apply_confirmation(
    analysis: Analysis,
    cluster: NewsCluster,
    *,
    big_claim_categories: tuple[str, ...],
    unconfirmed_weight: float,
) -> Analysis:
    """Big claims from one unofficial source stay low-weight until a second or official source."""
    triage = analysis.triage
    confirmed = cluster.has_official or cluster.confirmation_count >= 1
    big_claim = triage.category in big_claim_categories or triage.event_type == "rumor"
    single_unofficial = not cluster.has_official and cluster.confirmation_count == 0
    needs = (
        triage.needs_confirmation
        or triage.event_type == "rumor"
        or (big_claim and single_unofficial)
    ) and not confirmed
    # Scheduled releases parsed from numbers do not need a second source.
    if analysis.release is not None:
        needs = False
    return analysis.model_copy(
        update={
            "confirmed": confirmed,
            "weight": unconfirmed_weight if needs else 1.0,
            "triage": triage.model_copy(update={"needs_confirmation": needs}),
            "source_ids": list(dict.fromkeys(cluster.source_ids)),
            "item_ids": list(cluster.item_ids),
        }
    )

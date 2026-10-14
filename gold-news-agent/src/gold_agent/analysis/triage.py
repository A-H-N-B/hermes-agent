"""Triage (7.6) and deep analysis (7.7). Rules/fast path always produce an answer; the LLM enriches
it when available. Every LLM claim is checked against the source text before use."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import structlog

from gold_agent.analysis.drivers import DriverKB
from gold_agent.analysis.llm_client import LlmClient, LlmFailed, LlmUnavailable
from gold_agent.analysis.prompts import load_prompt, untrusted
from gold_agent.analysis.rules import release_triage, rules_triage
from gold_agent.analysis.verification import verify_triage
from gold_agent.config import Settings
from gold_agent.core.clock import Clock
from gold_agent.models import (
    Analysis,
    CalendarEvent,
    DeepAnalysis,
    MarketContext,
    NewsCluster,
    NewsItem,
    PrefilterResult,
    ReleaseReading,
    TriageResult,
)
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, fmt_wib

log = structlog.get_logger(__name__)


@dataclass
class TriageInputs:
    cluster: NewsCluster
    items: list[NewsItem]
    context: MarketContext | None = None
    events: list[CalendarEvent] = field(default_factory=lambda: list[CalendarEvent]())
    release: ReleaseReading | None = None
    fed_blackout: bool = False


def cluster_items(cluster: NewsCluster, stored: Sequence[NewsItem]) -> list[NewsItem]:
    """Items to show the model; falls back to the cluster lead when the archive lags behind."""
    if stored:
        return sorted(stored, key=lambda i: i.first_seen_at)
    from gold_agent.processing.normalizer import title_hash

    return [
        NewsItem(
            id=cluster.lead_item_id,
            source_id=cluster.lead_source_id,
            title=cluster.title,
            body=cluster.body,
            published_at=cluster.published_at,
            first_seen_at=cluster.first_seen_at,
            raw_hash=title_hash(cluster.title),
            tier=cluster.tier,
            reliability=cluster.reliability,
            official=cluster.has_official,
        )
    ]


def _fmt(value: float | None, pattern: str = "{:.2f}") -> str:
    return "n/a" if value is None else pattern.format(value)


def _context_block(context: MarketContext | None) -> str:
    if context is None or context.mid is None:
        return "data harga tidak tersedia"
    tf = context.timeframes
    h1, h4, m15 = tf.get("H1"), tf.get("H4"), tf.get("M15")
    session = SESSION_LABEL_ID.get(context.session, context.session)
    lines = [
        f"waktu_wib: {fmt_wib(context.ts, with_date=True)}; sesi: {session}; "
        f"pasar_buka: {'ya' if context.market_open else 'tidak'}",
        f"harga: {context.mid:.2f} ({context.source}{', proxy' if context.proxy else ''}); "
        f"spread: {_fmt(context.spread)}",
        f"tren H1: {h1.trend if h1 else 'n/a'}; tren H4: {h4.trend if h4 else 'n/a'}; "
        f"RSI H1: {_fmt(h1.rsi14 if h1 else None, '{:.0f}')}; "
        f"ATR M15: {_fmt(m15.atr14 if m15 else None)}",
        f"USD z15m: {_fmt(context.dxy_z_15m, '{:+.1f}')}, z1h: {_fmt(context.dxy_z_1h, '{:+.1f}')} "
        f"({context.dxy_source or 'n/a'})",
        f"yield 10Y: {_fmt(context.us10y)} (Δ1d {_fmt(context.us10y_change_1d, '{:+.2f}')}); "
        f"riil 10Y: {_fmt(context.real_yield_10y)} "
        f"(data harian per {context.yields_as_of or 'n/a'})",
    ]
    return "\n".join(lines)


def _events_block(events: Sequence[CalendarEvent]) -> str:
    if not events:
        return "tidak ada event terjadwal dalam ±2 jam"
    return "\n".join(
        f"- {fmt_wib(e.scheduled_at)} WIB {e.country} {e.title} "
        f"(forecast {e.forecast or '-'}, previous {e.previous or '-'}, "
        f"actual {e.actual or '-'}) [{e.impact}]"
        for e in events
    )


def build_user_message(inputs: TriageInputs, *, max_body_chars: int) -> str:
    items = "\n".join(
        f'<item id="{item.id}" sumber="{item.source_id}" tier="{item.tier}" '
        f'resmi="{"ya" if item.official else "tidak"}" '
        f'terbit="{item.published_at.isoformat() if item.published_at else "-"}" '
        f'diterima="{item.first_seen_at.isoformat()}">\n'
        f"JUDUL: {untrusted(item.title, 500)}\n"
        f"ISI: {untrusted(item.body, max_body_chars) or '-'}\n</item>"
        for item in inputs.items
    )
    notes = [f"periode blackout Fed: {'ya' if inputs.fed_blackout else 'tidak'}"]
    if inputs.release is not None:
        r = inputs.release
        notes.append(
            f"parser angka (deterministik): {r.event_name} aktual {r.actual:g} vs perkiraan "
            f"{r.forecast if r.forecast is not None else 'n/a'}; bucket {r.bucket}"
        )
    return (
        f"<berita>\n{items}\n</berita>\n"
        f"<konteks_pasar>\n{_context_block(inputs.context)}\n</konteks_pasar>\n"
        f"<kalender>\n{_events_block(inputs.events)}\n</kalender>\n"
        f"<catatan_sistem>\n" + "\n".join(f"- {n}" for n in notes) + "\n</catatan_sistem>\n"
        "Isi skema untuk cerita di atas. Gunakan hanya teks di <berita>; konteks hanya untuk "
        "menilai priced-in dan besarnya dampak."
    )


class TriageEngine:
    def __init__(
        self, settings: Settings, *, llm: LlmClient | None, kb: DriverKB, clock: Clock
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._kb = kb
        self._clock = clock
        self.prompt = load_prompt("triage", kb)
        self.deep_prompt = load_prompt("deep", kb)

    def _baseline(self, inputs: TriageInputs) -> TriageResult:
        if inputs.release is not None:
            return release_triage(inputs.release, [i.id for i in inputs.items])
        prefilter = inputs.cluster.prefilter or PrefilterResult(relevant=True)
        return rules_triage(
            inputs.cluster,
            prefilter,
            big_claim_categories=self._settings.analysis.big_claim_categories,
            fed_blackout=inputs.fed_blackout,
        )

    async def analyze(self, inputs: TriageInputs) -> Analysis:
        baseline = self._baseline(inputs)
        triage = baseline
        origin = "fast_path" if inputs.release is not None else "rules"
        model: str | None = None
        ai_pending = True
        notes: list[str] = []
        if self._llm is not None:
            message = build_user_message(
                inputs, max_body_chars=self._settings.analysis.max_body_chars
            )
            high_impact = inputs.release is not None or baseline.impact_score >= 4
            for attempt in (1, 2):
                try:
                    result = await self._llm.structured(
                        "triage",
                        system=self.prompt.text,
                        user=message,
                        schema=TriageResult,
                        ref_id=inputs.cluster.id,
                        high_impact=high_impact,
                    )
                except LlmUnavailable as exc:
                    notes.append(f"LLM tidak dipakai: {exc}")
                    break
                except LlmFailed as exc:
                    notes.append(f"LLM gagal (percobaan {attempt}): {exc}")
                    if exc.invalid_output and attempt == 1:
                        continue
                    break
                verified, checks = verify_triage(result.value, inputs.items)
                notes.extend(checks)
                if inputs.release is not None:
                    # Numbers + direction come from the deterministic parser; LLM adds context.
                    verified = verified.model_copy(
                        update={
                            "event_type": "scheduled_release",
                            "direction_gold": baseline.direction_gold,
                            "impact_score": max(baseline.impact_score, verified.impact_score),
                            "surprise": baseline.surprise,
                            "is_gold_relevant": True,
                        }
                    )
                else:
                    origin = "llm"
                triage = verified
                model = result.model
                ai_pending = False
                break
        now = self._clock.now()
        cluster = inputs.cluster
        return Analysis(
            id=cluster.id,
            cluster_id=cluster.id,
            title=cluster.title,
            source_ids=list(dict.fromkeys(cluster.source_ids)),
            item_ids=list(cluster.item_ids),
            triage=triage,
            origin=origin,  # type: ignore[arg-type]
            model=model,
            prompt_version=self.prompt.version if model else None,
            ai_pending=ai_pending,
            first_seen_at=cluster.first_seen_at,
            published_at=cluster.published_at,
            triaged_at=now,
            half_life_minutes=self._kb.half_life(triage.category),
            reliability=cluster.reliability,
            release=inputs.release,
            notes=notes,
        )

    def needs_deep(self, analysis: Analysis) -> bool:
        triage = analysis.triage
        if not triage.is_gold_relevant:
            return False
        if triage.impact_score >= self._settings.llm.deep_min_impact:
            return True
        return analysis.release is not None and self._settings.analysis.deep_on_scheduled_release

    async def deepen(self, analysis: Analysis, inputs: TriageInputs) -> Analysis:
        """Deep analysis with the larger model; returns the analysis unchanged when unavailable."""
        if self._llm is None:
            return analysis
        message = (
            build_user_message(inputs, max_body_chars=self._settings.analysis.max_body_chars)
            + "\n<triage>\n"
            + analysis.triage.model_dump_json(
                include={
                    "category",
                    "event_type",
                    "direction_gold",
                    "impact_score",
                    "confidence",
                    "priced_in_risk",
                    "needs_confirmation",
                    "summary_id",
                    "reasoning_id",
                }
            )
            + "\n</triage>"
        )
        try:
            result = await self._llm.structured(
                "deep",
                system=self.deep_prompt.text,
                user=message,
                schema=DeepAnalysis,
                ref_id=analysis.cluster_id,
                high_impact=analysis.triage.impact_score >= 4,
            )
        except (LlmUnavailable, LlmFailed) as exc:
            log.info("deep.skipped", cluster=analysis.cluster_id, reason=str(exc))
            return analysis.model_copy(
                update={"notes": [*analysis.notes, f"analisis mendalam: {exc}"]}
            )
        corpus = " ".join(f"{i.title} {i.body or ''}" for i in inputs.items).lower()
        deep = result.value.model_copy(
            update={"evidence": [e for e in result.value.evidence if e.strip().lower() in corpus]}
        )
        return analysis.model_copy(
            update={"deep": deep, "deep_model": result.model, "analyzed_at": self._clock.now()}
        )

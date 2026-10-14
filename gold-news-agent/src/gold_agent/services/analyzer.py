"""Analyzer service: news.raw -> prefilter -> fast path / triage -> verification -> news.analyses,
plus price-led volatility detection on market.context."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import structlog

from gold_agent.analysis.drivers import DriverKB
from gold_agent.analysis.llm_client import LlmClient
from gold_agent.analysis.price_led import PriceLedDetector, VolatilitySpike
from gold_agent.analysis.triage import TriageEngine, TriageInputs, cluster_items
from gold_agent.analysis.verification import apply_confirmation
from gold_agent.core.app import AppContext
from gold_agent.core.bus import (
    STREAM_ALERTS,
    STREAM_ANALYSES,
    STREAM_CONTEXT,
    STREAM_EVENTS,
    STREAM_NEWS_RAW,
    STREAM_RELEASES,
    Message,
    consume,
)
from gold_agent.econ_calendar.calibration import SurpriseCalibration
from gold_agent.econ_calendar.events import load_events
from gold_agent.econ_calendar.fomc import FomcMeeting, in_blackout
from gold_agent.econ_calendar.release_parser import ReleaseParser
from gold_agent.econ_calendar.sync import load_fomc_meetings
from gold_agent.models import (
    Alert,
    Analysis,
    CalendarEvent,
    LatencyStage,
    MarketContext,
    NewsCluster,
    ReleaseReading,
)
from gold_agent.processing.prefilter import Prefilter

log = structlog.get_logger(__name__)


@dataclass
class _PendingSpike:
    spike: VolatilitySpike
    alert_id: str
    deadline: datetime


def _alert_id(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]  # noqa: S324


class AnalyzerService:
    name = "analyzer"

    def __init__(self, ctx: AppContext, *, anthropic_client: Any | None = None) -> None:
        self._ctx = ctx
        settings = ctx.settings
        self.prefilter = Prefilter.from_file()
        self.catalog = load_events()
        self.parser = ReleaseParser(
            self.catalog, window_minutes=settings.analysis.release_calendar_window_minutes
        )
        self.kb = DriverKB.from_file()
        self.llm = LlmClient(
            settings,
            api_key=ctx.env.get("ANTHROPIC_API_KEY"),
            ops=ctx.ops,
            clock=ctx.clock,
            client=anthropic_client,
        )
        self.engine = TriageEngine(settings, llm=self.llm, kb=self.kb, clock=ctx.clock)
        self.detector = PriceLedDetector(
            k=settings.price_led.atr_multiple_k,
            cooldown_minutes=settings.analysis.volatility_cooldown_minutes,
            spread_multiple=settings.analysis.spread_spike_multiple,
        )
        self.context: MarketContext | None = None
        self.recent: dict[str, Analysis] = {}
        self.pending: list[_PendingSpike] = []
        self.fomc: list[FomcMeeting] = []
        self.calibration = SurpriseCalibration(self.catalog, ctx.news)
        self._tasks: set[asyncio.Task[None]] = set()
        self._llm_state: str | None = None

    # ------------------------------------------------------------------ news

    async def handle_message(self, message: Message) -> None:
        await self.handle_cluster(NewsCluster.model_validate(message.data))

    async def handle_cluster(self, cluster: NewsCluster) -> Analysis | None:
        if cluster.is_new:
            return await self._new_story(cluster)
        return await self._story_update(cluster)

    async def _new_story(self, cluster: NewsCluster) -> Analysis | None:
        ctx = self._ctx
        now = ctx.clock.now()
        prefilter = self.prefilter.check(f"{cluster.title} {cluster.body or ''}")
        cluster = cluster.model_copy(update={"prefilter": prefilter})
        await ctx.news.upsert_cluster(cluster)
        if not prefilter.relevant or cluster.backfill:
            return None
        max_age = timedelta(minutes=ctx.settings.analysis.triage_max_age_minutes)
        if now - cluster.first_seen_at > max_age:
            return None
        events = await ctx.calendar.between(now - timedelta(hours=2), now + timedelta(hours=2))
        release = self._parse_release(cluster, events, now)
        if release is not None:
            await self._on_release(release, events)
        items = cluster_items(cluster, await ctx.news.items(cluster.item_ids))
        inputs = TriageInputs(
            cluster=cluster,
            items=items,
            context=self.context,
            events=[e for e in events if e.impact in ("high", "medium")],
            release=release,
            fed_blackout=in_blackout(now, self.fomc) is not None,
        )
        analysis = await self.engine.analyze(inputs)
        analysis = apply_confirmation(
            analysis,
            cluster,
            big_claim_categories=ctx.settings.analysis.big_claim_categories,
            unconfirmed_weight=ctx.settings.news.unconfirmed_weight,
        )
        await self._publish(analysis, "triaged")
        await self._llm_health_alerts()
        if analysis.triage.is_gold_relevant and self.engine.needs_deep(analysis):
            task = asyncio.create_task(self._deep(analysis, inputs))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        if analysis.triage.is_gold_relevant and analysis.triage.impact_score >= 4:
            await ctx.bus.publish(
                STREAM_EVENTS,
                {"type": "outlook.refresh", "reason": analysis.title[:120], "ref_id": analysis.id},
            )
        await self._link_pending(analysis)
        return analysis

    def _parse_release(
        self, cluster: NewsCluster, events: list[CalendarEvent], now: datetime
    ) -> ReleaseReading | None:
        for text in (cluster.title, *(cluster.body or "").splitlines()[:3]):
            if not text.strip():
                continue
            reading = self.parser.parse(
                text,
                now=now,
                first_seen_at=cluster.first_seen_at,
                calendar=events,
                cluster_id=cluster.id,
                source_id=cluster.lead_source_id,
                std_provider=self.calibration.std_for,
            )
            if reading is not None:
                return reading
        return None

    async def _on_release(self, release: ReleaseReading, events: list[CalendarEvent]) -> None:
        ctx = self._ctx
        await ctx.bus.publish(STREAM_RELEASES, release)
        await ctx.news.save_release(release)
        await self.calibration.refresh()
        await ctx.ops.record_latency(
            LatencyStage(
                ref_id=release.cluster_id or release.event_key,
                source_id=release.source_id or "-",
                stage="flash",
                ts=release.parsed_at,
                seconds_from_first_seen=(release.parsed_at - release.first_seen_at).total_seconds(),
            )
        )
        window = timedelta(minutes=ctx.settings.analysis.release_calendar_window_minutes)
        for event in events:
            if (
                event.event_key == release.event_key
                and abs(event.scheduled_at - release.parsed_at) <= window
            ):
                await ctx.calendar.set_actual(event.id, f"{release.actual:g}{release.unit}")

    async def _story_update(self, cluster: NewsCluster) -> Analysis | None:
        existing = self.recent.get(cluster.id) or await self._ctx.news.get_analysis(cluster.id)
        if existing is None:
            return None
        updated = apply_confirmation(
            existing,
            cluster,
            big_claim_categories=self._ctx.settings.analysis.big_claim_categories,
            unconfirmed_weight=self._ctx.settings.news.unconfirmed_weight,
        )
        if (updated.confirmed, updated.weight, updated.source_ids) != (
            existing.confirmed,
            existing.weight,
            existing.source_ids,
        ):
            await self._publish(updated, None)
        return updated

    async def _deep(self, analysis: Analysis, inputs: TriageInputs) -> None:
        try:
            deep = await self.engine.deepen(analysis, inputs)
        except Exception:
            log.exception("deep.failed", cluster=analysis.cluster_id)
            return
        if deep.deep is None:
            return
        latest = self.recent.get(analysis.cluster_id, analysis)
        merged = latest.model_copy(
            update={
                "deep": deep.deep,
                "deep_model": deep.deep_model,
                "analyzed_at": deep.analyzed_at,
            }
        )
        await self._publish(merged, "analyzed")

    async def _publish(
        self, analysis: Analysis, stage: Literal["triaged", "analyzed"] | None
    ) -> None:
        ctx = self._ctx
        analysis = analysis.model_copy(update={"version_at": ctx.clock.now()})
        await ctx.news.save_analysis(analysis)
        await ctx.bus.publish(STREAM_ANALYSES, analysis)
        self.recent[analysis.cluster_id] = analysis
        cutoff = ctx.clock.now() - timedelta(hours=6)
        for key in [k for k, a in self.recent.items() if a.first_seen_at < cutoff]:
            del self.recent[key]
        if stage is not None:
            ts = (
                analysis.analyzed_at
                if stage == "analyzed" and analysis.analyzed_at
                else analysis.triaged_at
            )
            await ctx.ops.record_latency(
                LatencyStage(
                    ref_id=analysis.cluster_id,
                    source_id=analysis.source_ids[0] if analysis.source_ids else "-",
                    stage=stage,
                    ts=ts,
                    seconds_from_first_seen=(ts - analysis.first_seen_at).total_seconds(),
                )
            )

    async def _alert(self, alert: Alert) -> None:
        await self._ctx.ops.save_alert(alert)
        await self._ctx.bus.publish(STREAM_ALERTS, alert)

    async def _llm_health_alerts(self) -> None:
        now = self._ctx.clock.now()
        state = (
            "circuit_open"
            if self.llm.breaker.is_open
            else "over_budget"
            if self.llm.budget_exceeded
            else "ok"
        )
        if state == self._llm_state:
            return
        previous, self._llm_state = self._llm_state, state
        if state == "circuit_open":
            await self._alert(
                Alert(
                    id=_alert_id("llm", state, now),
                    kind="health",
                    severity="critical",
                    title="Claude API bermasalah — mode rules-only",
                    detail=(
                        "Kegagalan beruntun membuka circuit breaker; "
                        "analisis memakai aturan sampai pulih."
                    ),
                    created_at=now,
                    dedupe_key="llm_circuit",
                )
            )
        elif state == "over_budget":
            await self._alert(
                Alert(
                    id=_alert_id("llm", state, now),
                    kind="budget",
                    severity="warning",
                    title="Budget LLM harian habis",
                    detail=(
                        "Hanya berita dampak tinggi yang dianalisis LLM; sisanya memakai aturan."
                    ),
                    created_at=now,
                    dedupe_key="llm_budget",
                )
            )
        elif previous is not None:
            await self._alert(
                Alert(
                    id=_alert_id("llm", state, now),
                    kind="recovery",
                    severity="info",
                    title="Analisis LLM normal kembali",
                    detail="Claude API merespons normal.",
                    created_at=now,
                    dedupe_key="llm_recovery",
                )
            )

    # ------------------------------------------------------------------ price-led detection

    async def handle_context(self, message: Message) -> None:
        context = MarketContext.model_validate(message.data)
        self.context = context
        for spike in self.detector.update(context):
            await self._on_spike(spike)
        self.pending = [p for p in self.pending if p.deadline > context.ts]

    async def _on_spike(self, spike: VolatilitySpike) -> None:
        ctx = self._ctx
        lookback = timedelta(minutes=ctx.settings.price_led.news_lookback_minutes)
        linked = [
            a
            for a in self.recent.values()
            if a.triage.is_gold_relevant and spike.ts - lookback <= a.first_seen_at <= spike.ts
        ]
        if spike.kind == "move":
            arrow = "naik" if spike.direction == "up" else "turun"
            title = f"Lonjakan harga: {arrow} ${abs(spike.move):.2f} dalam 1 menit"
            detail = (
                f"Gerak melebihi ambang ${spike.threshold:.2f} (k × ATR M1) "
                f"di harga {spike.price:.2f}."
            )
        else:
            title = "Spread melebar tajam"
            detail = f"Spread {spike.spread:.2f} > ambang {spike.threshold:.2f} (×median 30 mnt)."
        if linked:
            detail += " Kemungkinan pemicu: " + "; ".join(a.title[:120] for a in linked[:3])
        else:
            detail += " Penyebab belum diketahui — mencari berita terkait."
        alert = Alert(
            id=_alert_id("spike", spike.kind, spike.ts),
            kind="volatility" if spike.kind == "move" else "spread",
            severity="warning",
            title=title,
            detail=detail,
            created_at=spike.ts,
            dedupe_key=f"spike:{spike.kind}:{spike.direction}",
            ref_ids=[a.cluster_id for a in linked],
        )
        await self._alert(alert)
        if not linked and spike.kind == "move":
            self.pending.append(_PendingSpike(spike, alert.id, spike.ts + timedelta(minutes=15)))
            await ctx.bus.publish(
                STREAM_EVENTS,
                {
                    "type": "collector.poll_now",
                    "source_ids": list(ctx.settings.analysis.targeted_search_sources),
                    "reason": alert.id,
                },
            )

    async def _link_pending(self, analysis: Analysis) -> None:
        if not analysis.triage.is_gold_relevant or not self.pending:
            return
        lookback = timedelta(minutes=self._ctx.settings.price_led.news_lookback_minutes)
        remaining: list[_PendingSpike] = []
        for pending in self.pending:
            if pending.spike.ts - lookback <= analysis.first_seen_at <= pending.deadline:
                lag = (analysis.first_seen_at - pending.spike.ts).total_seconds()
                when = f"{lag:.0f} dtk setelah lonjakan" if lag >= 0 else "sebelum lonjakan"
                await self._alert(
                    Alert(
                        id=_alert_id("spike-cause", pending.alert_id, analysis.cluster_id),
                        kind="volatility",
                        severity="info",
                        title="Penyebab lonjakan kemungkinan ditemukan",
                        detail=f"{analysis.title[:200]} (diterima {when}).",
                        created_at=self._ctx.clock.now(),
                        dedupe_key=f"spike-cause:{pending.alert_id}",
                        ref_ids=[analysis.cluster_id, pending.alert_id],
                    )
                )
            else:
                remaining.append(pending)
        self.pending = remaining

    # ------------------------------------------------------------------ run

    async def run(self, stop: asyncio.Event) -> None:
        self.fomc = await load_fomc_meetings(self._ctx)
        await self.calibration.refresh()
        bus = self._ctx.bus
        tasks = [
            asyncio.create_task(
                consume(bus, STREAM_NEWS_RAW, "analyzer", "analyzer-1", self.handle_message, stop)
            ),
            asyncio.create_task(
                consume(
                    bus,
                    STREAM_CONTEXT,
                    "analyzer-context",
                    "analyzer-1",
                    self.handle_context,
                    stop,
                    start="latest",
                )
            ),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in [*tasks, *self._tasks]:
                task.cancel()

"""Run the triage evaluation: prefilter, rules/fast path and (opt-in, costs money) the LLM."""

from __future__ import annotations

import time
from collections.abc import Sequence

from gold_agent.analysis.drivers import DriverKB
from gold_agent.analysis.llm_client import LlmClient
from gold_agent.analysis.rules import release_triage, rules_triage
from gold_agent.analysis.triage import TriageEngine, TriageInputs
from gold_agent.core.app import AppContext
from gold_agent.econ_calendar.events import load_events
from gold_agent.econ_calendar.release_parser import ReleaseParser
from gold_agent.evaluation.labels import HeadlineLabel
from gold_agent.evaluation.triage_eval import EvalReport, Prediction, score
from gold_agent.models import NewsCluster, NewsItem
from gold_agent.processing.prefilter import Prefilter


def single_item_cluster(item: NewsItem) -> NewsCluster:
    return NewsCluster(
        id=item.id,
        title=item.title,
        lead_item_id=item.id,
        lead_source_id=item.source_id,
        item_ids=[item.id],
        source_ids=[item.source_id],
        first_seen_at=item.first_seen_at,
        published_at=item.published_at,
        updated_at=item.first_seen_at,
        tier=item.tier,
        reliability=item.reliability,
        has_official=item.official,
        body=item.body,
    )


async def evaluate(
    ctx: AppContext, labels: Sequence[HeadlineLabel], *, use_llm: bool
) -> list[EvalReport]:
    items = {i.id: i for i in await ctx.news.items([label.item_id for label in labels])}
    known = [label for label in labels if label.item_id in items]
    prefilter = Prefilter.from_file()
    parser = ReleaseParser(load_events())
    big_claims = ctx.settings.analysis.big_claim_categories

    keyword: list[Prediction] = []
    rules: list[Prediction] = []
    clusters: dict[str, NewsCluster] = {}
    for label in known:
        item = items[label.item_id]
        result = prefilter.check(f"{item.title} {item.body or ''}")
        keyword.append(Prediction(item.id, result.relevant, None, None))
        cluster = single_item_cluster(item).model_copy(update={"prefilter": result})
        clusters[item.id] = cluster
        release = parser.parse(item.title, now=item.first_seen_at, first_seen_at=item.first_seen_at)
        triage = (
            release_triage(release, [item.id])
            if release is not None
            else rules_triage(cluster, result, big_claim_categories=big_claims)
        )
        rules.append(
            Prediction(
                item.id,
                result.relevant and triage.is_gold_relevant,
                triage.direction_gold,
                triage.impact_score,
            )
        )
    reports = [
        score("prefilter kata kunci", known, keyword),
        score("aturan + parser", known, rules),
    ]

    if use_llm:
        client = LlmClient(
            ctx.settings, api_key=ctx.env.get("ANTHROPIC_API_KEY"), ops=ctx.ops, clock=ctx.clock
        )
        engine = TriageEngine(ctx.settings, llm=client, kb=DriverKB.from_file(), clock=ctx.clock)
        llm: list[Prediction] = []
        for label in known:
            item = items[label.item_id]
            started = time.perf_counter()
            analysis = await engine.analyze(TriageInputs(cluster=clusters[item.id], items=[item]))
            elapsed = time.perf_counter() - started
            if analysis.ai_pending:  # the LLM did not answer: do not score rules as LLM
                continue
            llm.append(
                Prediction(
                    item.id,
                    analysis.triage.is_gold_relevant,
                    analysis.triage.direction_gold,
                    analysis.triage.impact_score,
                    latency_seconds=elapsed,
                )
            )
        reports.append(score(f"LLM ({ctx.settings.llm.triage.model})", known, llm))
    return reports

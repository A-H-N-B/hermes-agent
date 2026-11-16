"""The shipped config spends the AI only where the program cannot read the news itself, so $5 of
credit lasts two months: easy stories (low impact, or a clear direction from the keywords) and
releases whose numbers the parser read never reach the AI; unclear or unconfirmed stories do."""

from __future__ import annotations

import pytest

from gold_agent.analysis.drivers import DriverKB
from gold_agent.analysis.llm_client import LlmClient
from gold_agent.analysis.triage import TriageEngine, TriageInputs, cluster_items
from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from tests.fakes import FakeAnthropic, parsed
from tests.test_llm_triage import cluster, triage_value


def engine(ctx: AppContext, fake: FakeAnthropic) -> TriageEngine:
    client = LlmClient(ctx.settings, api_key=None, ops=ctx.ops, clock=ctx.clock, client=fake)
    return TriageEngine(ctx.settings, llm=client, kb=DriverKB.from_file(), clock=ctx.clock)


@pytest.mark.parametrize(
    ("title", "why"),
    [
        ("Gold edges higher in quiet trade", "dampak 1/5 rendah"),
        ("Fed's Logan signals fewer rate cuts ahead", "arah jelas dari kata kunci (bearish)"),
    ],
)
async def test_easy_news_is_read_by_the_program_alone(
    ctx: AppContext, title: str, why: str
) -> None:
    fake = FakeAnthropic()
    story = cluster(title)
    analysis = await engine(ctx, fake).analyze(
        TriageInputs(cluster=story, items=cluster_items(story, []))
    )
    assert fake.requests == []
    assert analysis.origin == "rules" and not analysis.ai_pending  # final, not a stand-in
    assert analysis.notes == [f"dibaca mesin tanpa AI (hemat AI): {why}"]
    assert analysis.triage.summary_id.startswith("(Dibaca mesin)")


@pytest.mark.parametrize(
    "title",
    [
        "Fed's Powell says policy is well positioned",  # important, direction unclear
        "Missile strike on oil terminal lifts safe-haven gold",  # big claim, one unofficial source
    ],
)
async def test_unclear_or_unconfirmed_news_goes_to_the_ai(ctx: AppContext, title: str) -> None:
    fake = FakeAnthropic(parsed(triage_value()))
    story = cluster(title)
    analysis = await engine(ctx, fake).analyze(
        TriageInputs(cluster=story, items=cluster_items(story, []))
    )
    assert len(fake.requests) == 1 and analysis.origin == "llm"


def test_the_shipped_config_makes_five_dollars_last_two_months(settings: Settings) -> None:
    assert settings.budget.llm_monthly_usd == 2.5
    assert settings.llm.triage_min_impact == 3 and settings.llm.deep_min_impact == 4
    assert settings.llm.desk_votes == 1  # one quick answer per signal decision
    assert not settings.analysis.deep_on_scheduled_release
    assert not settings.outlook.use_llm and not settings.reports.use_llm  # tables need no AI

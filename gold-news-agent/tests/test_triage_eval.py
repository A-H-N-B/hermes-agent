from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gold_agent.core.app import AppContext
from gold_agent.evaluation.labels import (
    HeadlineLabel,
    agreement,
    export_template,
    label_payload,
    read_labels,
)
from gold_agent.evaluation.runner import evaluate
from gold_agent.evaluation.triage_eval import Prediction, render_report, score
from gold_agent.models import NewsItem
from gold_agent.processing.normalizer import title_hash

T0 = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)

HEADLINES = [
    ("Fed officials signal fewer rate cuts as inflation stays sticky", True, "bearish"),
    ("Dollar slides after weak US data", True, "bullish"),
    ("Missile strike hits Israeli city; Iran claims attack", True, "bullish"),
    ("US CPI MoM 0.5% vs 0.3% exp", True, "bearish"),
    ("Apple unveils a new iPhone", False, None),
    ("Swimmer wins gold medal at world championships", False, None),
]


def news(n: int, title: str) -> NewsItem:
    return NewsItem(
        id=f"feed:{n}",
        source_id="feed",
        title=title,
        first_seen_at=T0 + timedelta(minutes=n),
        raw_hash=title_hash(title),
        tier=1,
        reliability=0.8,
    )


def test_scores_and_report_are_honest_about_small_samples() -> None:
    labels = [
        HeadlineLabel(item_id="a", title="x", relevant=True, direction="bearish"),
        HeadlineLabel(item_id="b", title="y", relevant=False),
    ]
    predictions = [
        Prediction("a", True, "bullish", 3, latency_seconds=1.2),
        Prediction("b", True, None, None, latency_seconds=0.8),
    ]
    report = score("test", labels, predictions)
    assert (report.tp, report.fp, report.precision) == (1, 1, 0.5)
    assert report.direction_accuracy == 0.0
    markdown = render_report([report])
    assert "target ≥ 100" in markdown and "❌" in markdown
    assert "bearish → bullish: 1" in markdown


def test_label_agreement_between_two_labellers() -> None:
    a = [
        HeadlineLabel(item_id="1", title="t", relevant=True, direction="bearish"),
        HeadlineLabel(item_id="2", title="t", relevant=False),
    ]
    b = [
        HeadlineLabel(item_id="1", title="t", relevant=True, direction="bullish"),
        HeadlineLabel(item_id="2", title="t", relevant=False),
    ]
    stats = agreement(a, b)
    assert stats["relevance"] == 1.0 and stats["direction"] == 0.0


async def test_export_label_import_and_evaluate_round_trip(ctx: AppContext, tmp_path: Path) -> None:
    items = [news(n, title) for n, (title, _, _) in enumerate(HEADLINES)]
    for item in items:
        await ctx.news.save_item(item)
    todo = tmp_path / "todo.jsonl"
    assert export_template(items, todo, limit=100) == len(items)
    rows = [json.loads(line) for line in todo.read_text(encoding="utf-8").splitlines()]
    assert all(row["relevant"] is None for row in rows)
    for row, (_, relevant, direction) in zip(rows, HEADLINES, strict=True):
        row.update(relevant=relevant, direction=direction)
    rows[-1]["relevant"] = None  # one row left unlabelled
    todo.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    labels, problems = read_labels(todo)
    assert len(labels) == len(HEADLINES) - 1 and problems and "belum dilabel" in problems[0]
    for label in labels:
        await ctx.ops.save_label(label.item_id, "ahnb", label_payload(label), T0)
    stored = [
        HeadlineLabel.model_validate({"item_id": i, **p}) for i, _, p in await ctx.ops.labels()
    ]
    reports = await evaluate(ctx, stored, use_llm=False)
    keyword, rules = reports
    assert keyword.precision == 1.0 and keyword.recall == 1.0
    assert rules.direction_accuracy == 1.0

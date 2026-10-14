"""Labelled headline set (Phase 3): export archived headlines, label them, import the labels.

Labels live in JSONL so they can be filled in any editor or spreadsheet, then imported:
  {"item_id": "...", "title": "...", "relevant": true, "direction": "bearish", "impact": 3}
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from gold_agent.models import NewsItem

LabelDirection = Literal["bullish", "bearish", "neutral", "mixed"]


class HeadlineLabel(BaseModel):
    item_id: str
    title: str
    relevant: bool
    direction: LabelDirection | None = None
    impact: int | None = Field(default=None, ge=0, le=5)
    notes: str | None = None


def export_template(items: Iterable[NewsItem], path: Path, *, limit: int) -> int:
    """Write unlabelled rows (relevant/direction/impact = null) for a human to fill in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            if count >= limit:
                break
            row = {
                "item_id": item.id,
                "source_id": item.source_id,
                "first_seen_at": item.first_seen_at.isoformat(),
                "title": item.title,
                "body": (item.body or "")[:400],
                "relevant": None,
                "direction": None,
                "impact": None,
                "notes": None,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_labels(path: Path) -> tuple[list[HeadlineLabel], list[str]]:
    """Parse filled rows; rows still missing `relevant` are skipped and reported."""
    labels: list[HeadlineLabel] = []
    problems: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"baris {number}: JSON tidak valid ({exc.msg})")
            continue
        if row.get("relevant") is None:
            problems.append(f"baris {number}: belum dilabel (relevant kosong)")
            continue
        try:
            labels.append(HeadlineLabel.model_validate(row))
        except ValueError as exc:
            problems.append(f"baris {number}: {exc}")
    return labels, problems


def agreement(first: Sequence[HeadlineLabel], second: Sequence[HeadlineLabel]) -> dict[str, float]:
    """How often two labellers agree on the items both labelled (label consistency check)."""
    by_id = {label.item_id: label for label in second}
    shared = [(a, by_id[a.item_id]) for a in first if a.item_id in by_id]
    if not shared:
        return {"items": 0.0}
    relevance = sum(a.relevant == b.relevant for a, b in shared) / len(shared)
    both_relevant = [(a, b) for a, b in shared if a.relevant and b.relevant]
    direction = (
        sum(a.direction == b.direction for a, b in both_relevant) / len(both_relevant)
        if both_relevant
        else float("nan")
    )
    return {"items": float(len(shared)), "relevance": relevance, "direction": direction}


def label_payload(label: HeadlineLabel) -> dict[str, object]:
    return label.model_dump(exclude={"item_id"})

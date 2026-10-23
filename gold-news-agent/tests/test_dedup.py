from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from gold_agent.models import NewsItem
from gold_agent.processing.dedup import Deduplicator
from gold_agent.processing.normalizer import stable_id, title_hash
from tests.conftest import T0


def item(title: str, source: str = "a", minutes: float = 0, **extra: Any) -> NewsItem:
    return NewsItem(
        id=stable_id(source, f"{title}-{minutes}"),
        source_id=source,
        title=title,
        first_seen_at=T0 + timedelta(minutes=minutes),
        raw_hash=title_hash(title),
        tier=extra.pop("tier", 1),
        reliability=extra.pop("reliability", 0.8),
        **extra,
    )


def dedup() -> Deduplicator:
    return Deduplicator(window_minutes=30, fuzzy_ratio=90)


def test_same_story_from_second_source_confirms_first() -> None:
    d = dedup()
    first = d.add(item("Fed holds rates steady at 4.25%", "squawk"))
    second = d.add(item("Fed holds rates steady at 4.25%", "fed", minutes=2, official=True))
    assert first.is_new and not second.is_new
    assert second.changed
    assert second.cluster.id == first.cluster.id
    assert second.cluster.confirmation_count == 1
    assert second.cluster.first_seen_at == T0  # the first source fixes the time
    assert second.cluster.has_official


def test_fuzzy_paraphrase_merges_but_different_numbers_do_not() -> None:
    d = dedup()
    base = d.add(item("Gold climbs as dollar weakens after jobs data", "a"))
    paraphrase = d.add(item("Gold climbs as the dollar weakens after jobs data", "b", 1))
    assert paraphrase.cluster.id == base.cluster.id
    cpi = d.add(item("US CPI MoM 0.4% vs 0.3% exp", "a", 2))
    other = d.add(item("US CPI MoM 0.2% vs 0.3% exp", "b", 2.5))
    assert other.is_new and other.cluster.id != cpi.cluster.id


def test_same_source_repeat_adds_no_confirmation() -> None:
    d = dedup()
    d.add(item("ECB leaves rates unchanged", "a"))
    again = d.add(item("ECB leaves rates unchanged", "a", 1))
    assert not again.changed
    assert again.cluster.confirmation_count == 0


def test_stories_expire_after_the_window() -> None:
    d = dedup()
    old = d.add(item("Oil spikes on supply fears", "a"))
    later = d.add(item("Oil spikes on supply fears", "b", 31))
    assert later.is_new and later.cluster.id != old.cluster.id


def test_backfilled_lead_keeps_cluster_out_of_triggers() -> None:
    d = dedup()
    d.add(item("Gold steady ahead of Fed", "a", backfill=True))
    joined = d.add(item("Gold steady ahead of Fed", "b", 1))
    assert joined.cluster.backfill


class FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.vectors.get(t, [0.0, 1.0]) for t in texts]


def test_embeddings_catch_paraphrases_fuzzy_matching_misses() -> None:
    vectors = {
        "fed signals pause in rate hikes": [1.0, 0.0],
        "central bank hints it will stop tightening": [0.99, 0.05],
    }
    d = Deduplicator(window_minutes=30, fuzzy_ratio=90, embedder=FakeEmbedder(vectors))
    a = d.add(item("Fed signals pause in rate hikes", "a"))
    b = d.add(item("Central bank hints it will stop tightening", "b", 1))
    assert b.cluster.id == a.cluster.id


def test_warm_restores_window_after_restart() -> None:
    d = dedup()
    cluster = d.add(item("Gold hits record high", "a")).cluster
    restarted = dedup()
    restarted.warm([cluster])
    again = restarted.add(item("Gold hits record high", "b", 3))
    assert again.cluster.id == cluster.id and again.cluster.confirmation_count == 1

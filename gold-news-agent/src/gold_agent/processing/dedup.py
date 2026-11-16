"""Dedup & clustering (7.3): exact hash, fuzzy title match, optional embeddings, 30-minute window.

The first source of a story fixes its time; later sources only add confirmations.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from rapidfuzz import fuzz

from gold_agent.models import NewsCluster, NewsItem
from gold_agent.processing.normalizer import normalize_title, title_hash, title_numbers


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


@dataclass
class _Entry:
    cluster: NewsCluster
    norm: str
    numbers: tuple[str, ...]
    hashes: set[str] = field(default_factory=lambda: set[str]())
    vector: list[float] | None = None


@dataclass(frozen=True)
class DedupResult:
    cluster: NewsCluster
    is_new: bool
    changed: bool  # a new source joined (confirmation count moved) or the cluster is new


class Deduplicator:
    def __init__(
        self,
        *,
        window_minutes: int,
        fuzzy_ratio: int,
        embedder: Embedder | None = None,
        embedding_cosine: float = 0.9,
    ) -> None:
        self._window = timedelta(minutes=window_minutes)
        self._ratio = fuzzy_ratio
        self._embedder = embedder
        self._cosine = embedding_cosine
        self._entries: list[_Entry] = []

    def __len__(self) -> int:
        return len(self._entries)

    def warm(self, clusters: Iterable[NewsCluster]) -> None:
        """Rebuild state after a restart from clusters updated within the window."""
        for cluster in clusters:
            entry = _Entry(cluster, normalize_title(cluster.title), title_numbers(cluster.title))
            entry.hashes.add(title_hash(cluster.title))
            self._entries.append(entry)

    def _expire(self, now: datetime) -> None:
        cutoff = now - self._window
        self._entries = [e for e in self._entries if e.cluster.updated_at >= cutoff]

    def _match(self, item: NewsItem, norm: str, numbers: tuple[str, ...]) -> _Entry | None:
        for entry in reversed(self._entries):
            if item.raw_hash in entry.hashes:
                return entry
        best: tuple[float, _Entry] | None = None
        for entry in self._entries:
            if numbers and entry.numbers and set(numbers) != set(entry.numbers):
                continue
            score = fuzz.token_sort_ratio(norm, entry.norm)
            if score >= self._ratio and (best is None or score > best[0]):
                best = (score, entry)
        if best is not None:
            return best[1]
        if self._embedder is None:
            return None
        vector = self._embedder.embed([norm])[0]
        for entry in self._entries:
            if numbers and entry.numbers and set(numbers) != set(entry.numbers):
                continue
            if entry.vector is None:
                entry.vector = self._embedder.embed([entry.norm])[0]
            if cosine(vector, entry.vector) >= self._cosine:
                return entry
        return None

    def add(self, item: NewsItem) -> DedupResult:
        self._expire(item.first_seen_at)
        norm = normalize_title(item.title)
        numbers = title_numbers(item.title)
        entry = self._match(item, norm, numbers)
        if entry is None:
            cluster = NewsCluster(
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
                backfill=item.backfill,
                is_new=True,
                body=item.body,
            )
            new_entry = _Entry(cluster, norm, numbers)
            new_entry.hashes.add(item.raw_hash)
            self._entries.append(new_entry)
            return DedupResult(cluster, is_new=True, changed=True)

        cluster = entry.cluster
        entry.hashes.add(item.raw_hash)
        if item.id in cluster.item_ids:
            return DedupResult(cluster, is_new=False, changed=False)
        new_source = item.source_id not in cluster.source_ids
        published = [p for p in (cluster.published_at, item.published_at) if p is not None]
        updated = cluster.model_copy(
            update={
                "item_ids": [*cluster.item_ids, item.id],
                "source_ids": [*cluster.source_ids, item.source_id]
                if new_source
                else cluster.source_ids,
                "updated_at": max(cluster.updated_at, item.first_seen_at),
                "published_at": min(published) if published else None,
                "tier": min(cluster.tier, item.tier),
                "reliability": max(cluster.reliability, item.reliability),
                "has_official": cluster.has_official or item.official,
                "is_new": False,
                "body": cluster.body or item.body,
            }
        )
        entry.cluster = updated
        return DedupResult(updated, is_new=False, changed=new_source)

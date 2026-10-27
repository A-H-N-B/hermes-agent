"""Fast keyword prefilter (7.4): relevant if >= 1 strong term, or >= 2 context terms.

Compiled once; a check is a handful of regex searches (well under 10 ms).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import yaml

from gold_agent.config import CONFIG_DIR
from gold_agent.models import PrefilterResult


def _compile(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w]){term}(?![\w])", re.I)


class Prefilter:
    def __init__(self, config: dict[str, Any]) -> None:
        self._strong = {
            group: [(term, _compile(term)) for term in terms]
            for group, terms in (config.get("strong") or {}).items()
        }
        self._context = {
            group: [(term, _compile(term)) for term in terms]
            for group, terms in (config.get("context") or {}).items()
        }
        self._negative = [_compile(term) for term in config.get("negative") or []]

    @classmethod
    def from_file(cls, path: Path = CONFIG_DIR / "keywords.yaml") -> Prefilter:
        with path.open(encoding="utf-8") as handle:
            return cls(yaml.safe_load(handle))

    def check(self, text: str) -> PrefilterResult:
        started = time.perf_counter()
        cleaned = text
        for pattern in self._negative:
            cleaned = pattern.sub(" ", cleaned)
        categories: list[str] = []
        matched: list[str] = []
        strong_terms = 0
        context_groups = 0
        for group, terms in self._strong.items():
            hits = [term for term, pattern in terms if pattern.search(cleaned)]
            if hits:
                strong_terms += len(hits)
                categories.append(group)
                matched.extend(hits)
        for group, terms in self._context.items():
            hits = [term for term, pattern in terms if pattern.search(cleaned)]
            if hits:
                context_groups += 1
                categories.append(group)
                matched.extend(hits)
        context_terms = len(matched) - strong_terms
        relevant = strong_terms >= 1 or context_groups >= 2 or context_terms >= 2
        return PrefilterResult(
            relevant=relevant,
            categories=categories,
            matched_terms=matched,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )

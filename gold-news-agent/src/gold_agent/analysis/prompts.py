"""Versioned system prompts (prompts/*.md) plus the driver knowledge base, rendered stably so the
prompt-cache prefix never changes between calls."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from gold_agent.analysis.drivers import DriverKB
from gold_agent.config import PROJECT_ROOT

PROMPT_DIR = PROJECT_ROOT / "prompts"


@dataclass(frozen=True)
class SystemPrompt:
    name: str
    text: str
    version: str  # first 10 hex of sha256(text): recorded with every analysis


def load_prompt(
    name: str, kb: DriverKB | None = None, directory: Path = PROMPT_DIR
) -> SystemPrompt:
    text = (directory / f"{name}_system.md").read_text(encoding="utf-8").strip()
    if kb is not None:
        text = f"{text}\n\n{kb.as_prompt_text()}"
    version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return SystemPrompt(name=name, text=text, version=version)


def untrusted(text: str | None, limit: int) -> str:
    """News text goes inside XML-ish tags: neutralise brackets so it cannot close our tags."""
    if not text:
        return ""
    cleaned = text.replace("<", "‹").replace(">", "›")
    return cleaned[:limit]

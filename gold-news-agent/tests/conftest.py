from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gold_agent.config import Secrets, Settings, Source, SourceRegistry, load_settings
from gold_agent.core.app import AppContext, build_context
from gold_agent.core.bus import MemoryBus
from gold_agent.core.clock import ManualClock

T0 = datetime(2026, 10, 13, 12, 30, tzinfo=UTC)  # Tuesday 19:30 WIB, US CPI time during US DST


def make_source(**overrides: Any) -> Source:
    base: dict[str, Any] = {
        "id": "feed",
        "name": "Feed",
        "kind": "news",
        "type": "rss",
        "url": "https://news.example.com/feed",
        "tier": 1,
        "poll_seconds": 10,
        "reliability": 0.8,
        "enabled": True,
        "status": "verified",
        "tos_note": "test",
    }
    return Source.model_validate(base | overrides)


@pytest.fixture
def settings() -> Settings:
    return load_settings()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> Secrets:
    for name in Secrets.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return Secrets(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.fixture
def ctx_factory(
    tmp_path: Path, settings: Settings, clock: ManualClock, secrets: Secrets
) -> Callable[..., AppContext]:
    def factory(
        sources: list[Source] | None = None,
        *,
        database_url: str | None = None,
        settings_override: Settings | None = None,
    ) -> AppContext:
        return build_context(
            settings=settings_override or settings,
            sources=SourceRegistry(sources=tuple(sources or [make_source()])),
            secrets=secrets,
            database_url=database_url or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            bus=MemoryBus(),
            clock=clock,
        )

    return factory


@pytest.fixture
async def ctx(ctx_factory: Callable[..., AppContext]) -> AsyncIterator[AppContext]:
    context = ctx_factory()
    await context.db.create_all()
    yield context
    await context.close()


def postgres_url() -> str | None:
    """Integration tests against a real PostgreSQL run when this is set (CI or local)."""
    return os.environ.get("GOLD_TEST_POSTGRES_URL")


def redis_url() -> str | None:
    return os.environ.get("GOLD_TEST_REDIS_URL")

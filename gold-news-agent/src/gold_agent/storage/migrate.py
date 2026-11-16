"""Alembic entry points used by `gold-agent db upgrade` and service start-up."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def upgrade(url: str, revision: str = "head") -> None:
    # env.py runs its own event loop, so Alembic must run off the current loop.
    await asyncio.to_thread(command.upgrade, alembic_config(url), revision)

"""Single-computer backup: a consistent copy of the live SQLite database, rotated."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from gold_agent.core.app import AppContext
from gold_agent.storage.backup import backup_sqlite, sqlite_file
from tests.conftest import T0


async def test_backup_copies_the_live_database_and_keeps_the_newest(
    ctx: AppContext, tmp_path: Path
) -> None:
    await ctx.ops.kv_set("probe", {"ok": True}, T0)  # the context keeps its connection open
    database = sqlite_file(ctx.db.url)
    assert database is not None
    target = tmp_path / "backups"
    first = backup_sqlite(database, target, now=T0, keep=2)
    backup_sqlite(database, target, now=T0 + timedelta(days=1), keep=2)
    newest = backup_sqlite(database, target, now=T0 + timedelta(days=2), keep=2)
    assert sorted(p.name for p in target.iterdir()) == [
        "gold-20261014T123000Z.db",
        "gold-20261015T123000Z.db",
    ]
    assert not first.exists()
    with closing(sqlite3.connect(newest)) as copy:
        assert copy.execute("SELECT key FROM kv_state").fetchall() == [("probe",)]


def test_only_sqlite_files_can_be_backed_up_this_way(tmp_path: Path) -> None:
    assert sqlite_file("postgresql+asyncpg://gold@db/gold") is None
    assert sqlite_file("sqlite+aiosqlite:///:memory:") is None
    assert sqlite_file(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}") == tmp_path / "x.db"

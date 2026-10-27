"""SQLite backup for single-computer installs (PostgreSQL: scripts/backup.sh with pg_dump).

Uses SQLite's online backup API, so the copy is consistent while the agent keeps writing, and
works on Windows where there is no sqlite3 command line tool.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


def sqlite_file(url: str) -> Path | None:
    """The database file behind a SQLite URL, or None for PostgreSQL / in-memory."""
    if not url.startswith("sqlite") or ":memory:" in url:
        return None
    return Path(url.split("///", 1)[-1])


def backup_sqlite(database: Path, target_dir: Path, *, now: datetime, keep: int) -> Path:
    """Copy `database` to target_dir/gold-<UTC stamp>.db and keep only the newest `keep`."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"gold-{now:%Y%m%dT%H%M%SZ}.db"
    partial = target.with_name(target.name + ".partial")
    source_uri = database.resolve().as_uri() + "?mode=ro"  # never creates an empty database
    with (
        closing(sqlite3.connect(source_uri, uri=True)) as source,
        closing(sqlite3.connect(partial)) as copy,
    ):
        source.backup(copy)
    partial.replace(target)
    for old in sorted(target_dir.glob("gold-*.db"))[:-keep]:
        old.unlink()
    return target

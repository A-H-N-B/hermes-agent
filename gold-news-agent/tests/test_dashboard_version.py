"""The dashboard header: today's move from any context, and which revision runs (read from
the checkout's .git without a git binary), so an update that never reached the running
program is visible."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from gold_agent.core.app import AppContext
from gold_agent.dashboard.app import create_app
from gold_agent.dashboard.live import LiveHub
from gold_agent.dashboard.moves import price_moves
from gold_agent.dashboard.version import UNKNOWN, running_commit
from tests.test_moves import MONDAY, bar, context

SHA = "07cae75d1234567890abcdef1234567890abcdef"


def checkout(tmp_path: Path, *, packed: bool) -> Path:
    git = tmp_path / ".git"
    (git / "refs" / "heads" / "claude").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/claude/branch\n")
    if packed:
        (git / "packed-refs").write_text(f"# pack-refs\n{SHA} refs/heads/claude/branch\n")
    else:
        (git / "refs" / "heads" / "claude" / "branch").write_text(SHA + "\n")
    package = tmp_path / "gold-news-agent" / "src" / "gold_agent"
    package.mkdir(parents=True)
    return package


def test_the_running_commit_comes_from_a_loose_or_packed_ref(tmp_path: Path) -> None:
    assert running_commit(checkout(tmp_path / "a", packed=False)) == "07cae75d"
    assert running_commit(checkout(tmp_path / "b", packed=True)) == "07cae75d"
    assert running_commit(tmp_path / "nowhere") == UNKNOWN  # not a checkout


async def test_the_dashboard_serves_the_running_commit(ctx: AppContext) -> None:
    app = create_app(ctx, LiveHub(), password=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        commit = (await http.get("/api/version")).json()["commit"]
    assert commit == running_commit()
    assert commit != ""


def test_today_comes_from_the_stored_bars_when_the_context_lacks_the_days_open() -> None:
    """A context without day_open (written before the field existed, or before the live daily
    bar formed) must still give today's percent: from the running daily bar, else from the
    M15 bars since the last daily close."""
    bare = context(day_open=None, day_open_at=None, day_high=None, day_low=None)
    opened = datetime(2026, 10, 11, 21, 0, tzinfo=UTC)  # Sun 17:00 New York
    running = bar("D1", opened, 24 * 60, 4010.0, open_=4000.0).model_copy(
        update={"complete": False}
    )
    today = price_moves(context=bare, daily=[running], fine=[], coarse=[], now=MONDAY)["today"]
    assert today["open"] == 4000.0 and today["pct"] == 0.5  # 4020 vs 4000

    friday = bar("D1", opened - timedelta(days=3), 24 * 60, 3990.0, open_=3980.0)
    fine = [
        bar("M15", opened + timedelta(minutes=15 * i), 15, 4000.0 + i, open_=4000.0 + i)
        for i in range(4)
    ]
    today = price_moves(context=bare, daily=[friday], fine=fine, coarse=[], now=MONDAY)["today"]
    assert today["open"] == 4000.0 and today["open_at"] == opened.isoformat()
    assert today["pct"] == 0.5

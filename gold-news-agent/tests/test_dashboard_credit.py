"""Claude credit on the dashboard: entered once, spent down by the agent's own cost log."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.dashboard.app import create_app
from gold_agent.dashboard.credit import credit_summary
from gold_agent.dashboard.live import LiveHub
from tests.builders import T0


def test_remaining_days_and_status_follow_the_burn_rate() -> None:
    summary = credit_summary(
        balance_usd=10.0,
        as_of=T0 - timedelta(days=7),
        spent_since=7.0,
        spent_window=7.0,
        now=T0,
    )
    assert summary["remaining_usd"] == 3.0 and summary["per_day_usd"] == 1.0
    assert summary["days_left"] == 3.0 and summary["status"] == "menipis"
    fresh = credit_summary(
        balance_usd=20.0, as_of=T0 - timedelta(minutes=5), spent_since=0.1, spent_window=0.1, now=T0
    )
    assert fresh["per_day_usd"] is None and fresh["status"] == "aman"  # too early for a rate
    gone = credit_summary(
        balance_usd=1.0, as_of=T0 - timedelta(days=2), spent_since=1.5, spent_window=1.5, now=T0
    )
    assert gone["status"] == "habis" and gone["days_left"] is None


async def _client(ctx: AppContext) -> httpx.AsyncClient:
    await ctx.db.create_all()
    app = create_app(ctx, LiveHub(), password=None)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8787"
    )


async def test_entered_balance_is_spent_down_by_logged_calls(
    ctx: AppContext, clock: ManualClock
) -> None:
    clock.set(T0)
    async with await _client(ctx) as http:
        assert (await http.get("/api/credit")).json()["balance"] is None
        saved = await http.post("/api/credit", json={"balance_usd": 12.5})
        assert saved.status_code == 200 and saved.json()["balance"]["remaining_usd"] == 12.5
        clock.set(T0 + timedelta(hours=2))
        await ctx.ops.add_llm_usage(
            ts=T0 + timedelta(hours=1), route="triage", model="haiku", cost_usd=0.5, ok=True
        )
        await ctx.ops.add_llm_usage(  # before the balance was entered: already in the console
            ts=T0 - timedelta(hours=1), route="triage", model="haiku", cost_usd=9.0, ok=True
        )
        credit = (await http.get("/api/credit")).json()
    assert credit["balance"]["remaining_usd"] == 12.0
    assert credit["spent_today_usd"] == pytest.approx(9.5)


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({"origin": "https://evil.example"}, {"balance_usd": 1}, 403),
        ({}, {"balance_usd": "banyak"}, 422),
        ({}, {"balance_usd": -1}, 422),
        ({}, {"balance_usd": True}, 422),
    ],
)
async def test_the_balance_cannot_be_set_cross_site_or_to_nonsense(
    ctx: AppContext, headers: dict[str, str], body: dict[str, object], status: int
) -> None:
    async with await _client(ctx) as http:
        response = await http.post("/api/credit", json=body, headers=headers)
        form = await http.post("/api/credit", content="balance_usd=5")
    assert response.status_code == status
    assert form.status_code == 415  # a plain HTML form post from another site is refused too

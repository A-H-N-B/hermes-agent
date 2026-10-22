"""Claude API credit left, estimated from the agent's own cost log.

Anthropic exposes no endpoint that returns a prepaid credit balance for an ordinary API key, so
the user types the balance shown in console.anthropic.com once (after each top-up) and the
dashboard subtracts every call logged in `llm_usage` since then. The burn rate (last 7 days, or
since the balance was entered if that is shorter) gives "habis dalam N hari". It drifts from the
console only if the same key is also used outside this agent.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from gold_agent.analysis.llm_client import wib_day_start
from gold_agent.core.app import AppContext
from gold_agent.timeutils.sessions import fmt_wib

JSON = dict[str, Any]
KV_CREDIT = "llm_credit"
LOW_DAYS = 7.0  # warn when the balance lasts less than a week at the current pace
LOW_USD = 2.0
RATE_WINDOW = timedelta(days=7)
MIN_RATE_WINDOW = timedelta(hours=1)  # a rate from a few minutes of calls is noise


def credit_summary(
    *,
    balance_usd: float,
    as_of: datetime,
    spent_since: float,
    spent_window: float,
    now: datetime,
) -> JSON:
    """Remaining credit and how long it lasts; `spent_window` covers min(7 days, since as_of)."""
    remaining = balance_usd - spent_since
    window = min(RATE_WINDOW, now - as_of)
    per_day = spent_window / (window / timedelta(days=1)) if window >= MIN_RATE_WINDOW else None
    days_left = remaining / per_day if per_day and remaining > 0 else None
    if remaining <= 0:
        status = "habis"
    elif remaining < LOW_USD or (days_left is not None and days_left < LOW_DAYS):
        status = "menipis"
    else:
        status = "aman"
    empty_at = now + timedelta(days=days_left) if days_left is not None else None
    return {
        "balance_usd": round(balance_usd, 2),
        "as_of": as_of.isoformat(),
        "as_of_wib": fmt_wib(as_of, with_date=True),
        "spent_since_usd": round(spent_since, 4),
        "remaining_usd": round(remaining, 2),
        "per_day_usd": round(per_day, 4) if per_day is not None else None,
        "days_left": round(days_left, 1) if days_left is not None else None,
        "empty_at_wib": fmt_wib(empty_at, with_date=True) if empty_at else None,
        "status": status,
    }


async def credit_view(ctx: AppContext) -> JSON:
    now = ctx.clock.now()
    spend = ctx.ops.llm_spend_since
    base: JSON = {
        "spent_today_usd": round(await spend(wib_day_start(now)), 4),
        "daily_budget_usd": ctx.settings.budget.llm_daily_usd,
        "routine_budget_usd": round(ctx.settings.budget.routine_daily_usd, 2),
        "has_key": bool(ctx.env.get("ANTHROPIC_API_KEY")),
    }
    stored = await ctx.ops.kv_get(KV_CREDIT)
    if not stored:
        return base | {"balance": None}
    as_of = datetime.fromisoformat(stored["as_of"])
    window_start = max(as_of, now - RATE_WINDOW)
    return base | {
        "balance": credit_summary(
            balance_usd=float(stored["balance_usd"]),
            as_of=as_of,
            spent_since=await spend(as_of),
            spent_window=await spend(window_start),
            now=now,
        )
    }


async def set_balance(ctx: AppContext, balance_usd: float) -> None:
    now = ctx.clock.now()
    await ctx.ops.kv_set(KV_CREDIT, {"balance_usd": balance_usd, "as_of": now.isoformat()}, now)

"""JSON views for the dashboard pages, straight from the repositories (no HTTP here)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Any

from gold_agent.analysis.llm_client import wib_day_start
from gold_agent.core.app import AppContext
from gold_agent.dashboard.forecast import SPAN, aggregate, forecast_next
from gold_agent.dashboard.forecast_score import Held, forecast_accuracy
from gold_agent.dashboard.indicators_view import (
    SUMMARY_TFS,
    WARMUP_BARS,
    indicator_series,
    indicator_summary,
    momentum,
)
from gold_agent.dashboard.timing import timing_view, trading_hours, trading_view
from gold_agent.evaluation.performance import summarize
from gold_agent.models import Analysis, CalendarEvent, Candle, MarketContext
from gold_agent.ops.metrics import summarize as latency_summary
from gold_agent.signals.engine import KV_ENGINE, KV_HALT, EngineState
from gold_agent.signals.tvcode import tradingview_code
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, fmt_wib

JSON = dict[str, Any]
FORECAST_BARS = 460  # enough for EMA200 to have forgotten its seed
ACCURACY_TTL = timedelta(minutes=5)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def context_view(context: MarketContext | None) -> JSON | None:
    if context is None:
        return None
    return {
        "ts": _iso(context.ts),
        "mid": context.mid,
        "bid": context.bid,
        "ask": context.ask,
        "spread": context.spread,
        "source": context.source,
        "proxy": context.proxy,
        "stale": context.stale,
        "tick_age_seconds": context.tick_age_seconds,
        "market_open": context.market_open,
        "session": SESSION_LABEL_ID.get(context.session, context.session),
    }


def news_view(analysis: Analysis) -> JSON:
    triage = analysis.triage
    return {
        "id": analysis.cluster_id,
        "title": analysis.title,
        "summary": triage.summary_id,
        "direction": triage.direction_gold,
        "impact": triage.impact_score,
        "category": triage.category,
        "relevant": triage.is_gold_relevant,
        "needs_confirmation": triage.needs_confirmation,
        "origin": analysis.origin,
        "ai_pending": analysis.ai_pending,
        "sources": analysis.source_ids,
        "first_seen_at": _iso(analysis.first_seen_at),
        "first_seen_wib": fmt_wib(analysis.first_seen_at, with_date=True),
        "latency_seconds": round((analysis.triaged_at - analysis.first_seen_at).total_seconds(), 1),
    }


def event_view(event: CalendarEvent, now: datetime) -> JSON:
    return {
        **event.model_dump(mode="json"),
        "wib": "sepanjang hari" if event.all_day else fmt_wib(event.scheduled_at, with_date=True),
        "seconds_until": (event.scheduled_at - now).total_seconds(),
    }


class DashboardData:
    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._accuracy: dict[str, tuple[datetime, JSON | None]] = {}

    async def _source(self) -> str | None:
        context = await self._ctx.market.latest_context()
        return context.source if context else None

    async def state(self) -> JSON:
        ctx = self._ctx
        now = ctx.clock.now()
        stored = await ctx.ops.kv_get(KV_ENGINE)
        engine = EngineState.from_json(stored) if stored else EngineState()
        state = await ctx.signals.latest_state()
        halt = await ctx.ops.kv_get(KV_HALT)
        events = await ctx.calendar.between(now, now + timedelta(days=7))
        upcoming = next((e for e in events if e.impact == "high" and not e.all_day), None)
        active = engine.active(now)
        return {
            "now": _iso(now),
            "mode": ctx.settings.mode,
            "state": state.model_dump(mode="json") if state else None,
            "active": active.model_dump(mode="json") if active else None,
            "context": context_view(await ctx.market.latest_context()),
            "halted": bool(halt and halt.get("halted")),
            "next_event": event_view(upcoming, now) if upcoming else None,
            "tradingview_symbol": ctx.settings.dashboard.tradingview_symbol,
            "tradingview_code": tradingview_code(active) if active else None,
            "trading": trading_view(trading_hours(ctx.settings), now),
        }

    async def candles(self, tf: str, limit: int) -> list[JSON]:
        bars = await self.bars(tf, limit)
        return [
            {
                "time": int(bar.open_time.timestamp()),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            }
            for bar in bars
        ]

    async def bars(self, tf: str, limit: int) -> list[Candle]:
        """Stored bars for M1..D1; W1 and MN built from D1."""
        source = await self._source()
        if tf in ("W1", "MN"):
            per = 7 if tf == "W1" else 31
            daily = await self._ctx.market.latest_candles(
                "D1", min(5000, limit * per), source=source
            )
            return aggregate(daily, tf)[-limit:]
        return await self._ctx.market.latest_candles(tf, limit, source=source)

    async def forecast(self, tf: str) -> JSON:
        ctx = self._ctx
        now = ctx.clock.now()
        stored = await ctx.ops.kv_get(KV_ENGINE)
        engine = EngineState.from_json(stored) if stored else EngineState()
        span_days = 40 if tf in ("W1", "MN") else 2
        forecast = forecast_next(
            tf=tf,
            bars=await self.bars(tf, FORECAST_BARS),
            now=now,
            settings=ctx.settings,
            state=await ctx.signals.latest_state(),
            active=engine.active(now),
            events=await ctx.calendar.between(
                now - timedelta(hours=1), now + timedelta(days=span_days)
            ),
            context=await ctx.market.latest_context(),
        )
        return forecast | {"accuracy": await self.forecast_accuracy(tf)}

    async def forecast_accuracy(self, tf: str) -> JSON | None:
        """Walk-forward hit rate of the forecast on this timeframe (cached for 5 minutes):
        the last 30 days (M1: 7), inside the trading windows that use this timeframe."""
        ctx = self._ctx
        now = ctx.clock.now()
        cached = self._accuracy.get(tf)
        if cached is not None and now - cached[0] < ACCURACY_TTL:
            return cached[1]
        result = None
        if tf not in ("W1", "MN"):
            result = await self._score_forecast(tf, now)
        self._accuracy[tf] = (now, result)
        return result

    async def _score_forecast(self, tf: str, now: datetime) -> JSON:
        ctx = self._ctx
        start = now - timedelta(days=7 if tf == "M1" else 30)
        warmup = start - SPAN[tf] * FORECAST_BARS
        bars = await ctx.market.candles(tf, warmup, now, source=await self._source())
        signals = await ctx.signals.signals_between(start - timedelta(days=2), now)
        closed = {o.signal_id: o.closed_at for o in await ctx.signals.outcomes_between(start, now)}
        held: list[Held] = sorted(
            (
                s.created_at_utc,
                min(s.valid_until_utc, closed.get(s.signal_id) or s.valid_until_utc),
                1.0 if s.action == "BUY" else -1.0,
            )
            for s in signals
        )
        windows = [w.name for w in ctx.settings.trading.windows if w.timeframe == tf]
        spans = None
        if windows:
            all_spans = trading_hours(ctx.settings).spans(start, now)
            spans = [s for s in all_spans if s.timeframe == tf]
        result = forecast_accuracy(
            tf=tf,
            bars=bars,
            scores=await ctx.signals.scores_between(start - timedelta(minutes=10), now),
            held=held,
            events=await ctx.calendar.between(start - timedelta(hours=1), now),
            spans=spans,
            threshold=ctx.settings.signal_engine.entry_threshold,
            confirm=timedelta(seconds=ctx.settings.trading.post_release_confirm_seconds),
            now=now,
            start=start,
        )
        return result | {"windows": windows, "days": (now - start).days}

    async def indicators(self, tf: str, limit: int) -> JSON:
        ctx = self._ctx
        source = await self._source()
        bars = await self.bars(tf, limit + WARMUP_BARS)
        extras = {
            name: momentum(await ctx.market.latest_candles(name, 120, source=source))
            for name in SUMMARY_TFS
        }
        return {
            "series": indicator_series(bars, limit),
            "summary": indicator_summary(await ctx.market.latest_context(), extras),
        }

    async def timing(self) -> JSON:
        ctx = self._ctx
        now = ctx.clock.now()
        stored = await ctx.ops.kv_get(KV_ENGINE)
        engine = EngineState.from_json(stored) if stored else EngineState()
        return timing_view(
            now=now,
            settings=ctx.settings,
            active=engine.active(now),
            state=await ctx.signals.latest_state(),
            outlook=await ctx.signals.latest_outlook(),
            events=await ctx.calendar.between(now - timedelta(hours=1), now + timedelta(days=2)),
            context=await ctx.market.latest_context(),
        )

    async def markers(self, hours: float) -> JSON:
        ctx = self._ctx
        now = ctx.clock.now()
        since = now - timedelta(hours=hours)
        news = [
            {
                "time": int(a.first_seen_at.timestamp()),
                "direction": a.triage.direction_gold,
                "impact": a.triage.impact_score,
                "title": a.triage.summary_id or a.title,
            }
            for a in await ctx.news.analyses_since(since)
            if a.triage.impact_score >= 3
        ]
        signals = [
            {"time": int(s.created_at_utc.timestamp()), "action": s.action, "id": s.signal_id}
            for s in await ctx.signals.signals_between(since, now + timedelta(seconds=1))
        ]
        return {"news": news, "signals": signals}

    async def news(self, limit: int) -> list[JSON]:
        analyses = await self._ctx.news.latest_analyses(limit=limit * 3)
        latest: dict[str, Analysis] = {}
        for analysis in analyses:  # newest version per story
            latest.setdefault(analysis.cluster_id, analysis)
        relevant = [a for a in latest.values() if a.triage.is_gold_relevant]
        ordered = sorted(relevant, key=lambda a: a.first_seen_at, reverse=True)
        return [news_view(a) for a in ordered[:limit]]

    async def outlook(self) -> JSON | None:
        outlook = await self._ctx.signals.latest_outlook()
        return outlook.model_dump(mode="json") if outlook else None

    async def calendar(self, days: float) -> list[JSON]:
        ctx = self._ctx
        now = ctx.clock.now()
        start, end = now - timedelta(hours=12), now + timedelta(days=days)
        playbooks = {p.event_id: p for p in await ctx.calendar.playbooks_between(start, end)}
        rows: list[JSON] = []
        for event in await ctx.calendar.between(start, end):
            if event.impact not in ("high", "medium"):
                continue
            row = event_view(event, now)
            playbook = playbooks.get(event.id)
            row["playbook"] = playbook.model_dump(mode="json") if playbook else None
            rows.append(row)
        return rows

    async def performance(self, days: float = 90) -> JSON:
        ctx = self._ctx
        now = ctx.clock.now()
        since, until = now - timedelta(days=days), now + timedelta(seconds=1)
        perf = summarize(
            await ctx.signals.signals_between(since, until),
            await ctx.signals.outcomes_between(since, until),
        )
        return dataclasses.asdict(perf)

    async def health(self) -> JSON:
        ctx = self._ctx
        now = ctx.clock.now()
        latency = {
            (s.source_id, s.stage): s
            for s in latency_summary(await ctx.ops.latency_samples(now - timedelta(days=1)))
        }
        sources: list[JSON] = []
        for health in await ctx.ops.source_health():
            row = health.model_dump(mode="json")
            published = latency.get((health.source_id, "published"))
            row["lag_p50"] = published.p50_seconds if published else None
            row["lag_p95"] = published.p95_seconds if published else None
            sources.append(row)
        stages = [
            s.model_dump()
            for (_, stage), s in sorted(latency.items())
            if stage in ("triaged", "analyzed", "flash", "signal", "notified")
        ]
        context = await ctx.market.latest_context()
        heartbeats = await ctx.ops.heartbeats()
        return {
            "now": _iso(now),
            "sources": sources,
            "stages": stages,
            "context": context_view(context),
            "context_age_seconds": (now - context.ts).total_seconds() if context else None,
            "llm_cost_today": round(await ctx.ops.llm_spend_since(wib_day_start(now)), 4),
            "llm_budget": ctx.settings.budget.llm_daily_usd,
            "heartbeats": {
                name: {"ts": _iso(ts), "age_seconds": (now - ts).total_seconds()}
                for name, ts in sorted(heartbeats.items())
            },
            "alerts": [a.model_dump(mode="json") for a in await ctx.ops.recent_alerts(limit=20)],
            "notifications": [
                {
                    "kind": n.kind,
                    "priority": n.priority,
                    "created_at": _iso(n.created_at),
                    "sent": n.sent_at is not None,
                    "reason": n.suppressed_reason,
                }
                for n in await ctx.notify.recent(limit=20)
            ],
        }

"""JSON views for the dashboard pages, straight from the repositories (no HTTP here)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from gold_agent.analysis.llm_client import wib_day_start
from gold_agent.core.app import AppContext
from gold_agent.dashboard.indicators_view import (
    SUMMARY_TFS,
    WARMUP_BARS,
    indicator_series,
    indicator_summary,
)
from gold_agent.dashboard.timing import timing_view
from gold_agent.evaluation.performance import summarize
from gold_agent.fundamentals.snapshot import KV_FUNDAMENTALS
from gold_agent.market.reads import momentum
from gold_agent.models import (
    Analysis,
    CalendarEvent,
    Candle,
    FundamentalSnapshot,
    MarketContext,
    SignalState,
)
from gold_agent.ops.metrics import summarize as latency_summary
from gold_agent.signals.accuracy import WARMUP_BARS as FORECAST_BARS
from gold_agent.signals.accuracy import AccuracyBook
from gold_agent.signals.desk import KV_DESK, compare_reviews
from gold_agent.signals.engine import KV_ENGINE, KV_HALT, EngineState
from gold_agent.signals.forecast import aggregate, forecast_next
from gold_agent.signals.timeframe import KV_TIMEFRAME, session_key
from gold_agent.signals.tvcode import tradingview_code
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, SessionClock, fmt_wib

if TYPE_CHECKING:  # live.py imports this module
    from gold_agent.dashboard.live import LiveHub

JSON = dict[str, Any]
# Stored bars only change when a bar completes, at a minute boundary (the market service writes
# it within a second or two): reads are kept for the minute, refreshed BAR_SETTLE after it turns.
BAR_SETTLE = timedelta(seconds=3)


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
        "notes": context.notes,
        "impulse": context.impulse.model_dump(mode="json") if context.impulse else None,
        "price_offset": context.price_offset,
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
    def __init__(self, ctx: AppContext, hub: LiveHub | None = None) -> None:
        self._ctx = ctx
        self._hub = hub
        self.book = AccuracyBook.shared(ctx)  # the signal service learns
        self._sessions = SessionClock(ctx.settings.sessions)
        self._bars: dict[tuple[str, int, str | None], tuple[datetime, list[Candle]]] = {}

    async def _latest(self, tf: str, limit: int, source: str | None) -> list[Candle]:
        """Stored bars, read once per minute (see BAR_SETTLE): the Live page asks every second."""
        minute = (self._ctx.clock.now() - BAR_SETTLE).replace(second=0, microsecond=0)
        key = (tf, limit, source)
        hit = self._bars.get(key)
        if hit is not None and hit[0] == minute:
            return hit[1]
        bars = await self._ctx.market.latest_candles(tf, limit, source=source)
        self._bars = {k: v for k, v in self._bars.items() if v[0] == minute} | {key: (minute, bars)}
        return bars

    async def _engine_state(self) -> SignalState | None:
        """The newest engine state: the one pushed every evaluation when it is newer than the
        stored one (stored only on change)."""
        stored = await self._ctx.signals.latest_state()
        pushed = self._hub.last_state if self._hub else None
        if pushed is None:
            return stored
        live = SignalState.model_validate(pushed)
        return live if stored is None or live.ts >= stored.ts else stored

    async def _context(self) -> MarketContext | None:
        """The newest market context: the one pushed every few seconds, else the stored one
        (stored once a minute; parsing it costs more than the push)."""
        pushed = self._hub.context() if self._hub else None
        return pushed if pushed is not None else await self._ctx.market.latest_context()

    async def _source(self) -> str | None:
        context = await self._context()
        return context.source if context else None

    async def state(self) -> JSON:
        ctx = self._ctx
        now = ctx.clock.now()
        stored = await ctx.ops.kv_get(KV_ENGINE)
        engine = EngineState.from_json(stored) if stored else EngineState()
        state = await self._engine_state()
        halt = await ctx.ops.kv_get(KV_HALT)
        events = await ctx.calendar.between(now, now + timedelta(days=7))
        upcoming = next((e for e in events if e.impact == "high" and not e.all_day), None)
        active = engine.active(now)
        return {
            "now": _iso(now),
            "mode": ctx.settings.mode,
            "state": state.model_dump(mode="json") if state else None,
            "active": active.model_dump(mode="json") if active else None,
            "context": context_view(await self._context()),
            "halted": bool(halt and halt.get("halted")),
            "next_event": event_view(upcoming, now) if upcoming else None,
            "tradingview_symbol": ctx.settings.dashboard.tradingview_symbol,
            "tradingview_code": tradingview_code(active) if active else None,
            "timeframe": await ctx.ops.kv_get(KV_TIMEFRAME),
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
            daily = await self._latest("D1", min(5000, limit * per), source)
            return aggregate(daily, tf)[-limit:]
        return await self._latest(tf, limit, source)

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
            state=await self._engine_state(),
            active=engine.active(now),
            events=await ctx.calendar.between(
                now - timedelta(hours=1), now + timedelta(days=span_days)
            ),
            context=await self._context(),
        )
        accuracy = await self.book.accuracy(tf, source=await self._source())
        session = session_key(self._sessions.session(now))
        return forecast | {
            "accuracy": accuracy,
            "session": session,
            "session_label": SESSION_LABEL_ID.get(session, session),
        }

    async def indicators(self, tf: str, limit: int, *, series: bool = True) -> JSON:
        """The chart overlays (`series`, only needed when the chart loads or a bar closes) and
        the read of every timeframe."""
        ctx = self._ctx
        source = await self._source()
        extras = {name: momentum(await self._latest(name, 120, source)) for name in SUMMARY_TFS}
        stored = await ctx.ops.kv_get(KV_FUNDAMENTALS)
        fundamentals = FundamentalSnapshot.model_validate(stored) if stored else None
        summary = indicator_summary(await self._context(), extras, fundamentals)
        if not series:
            return {"summary": summary}
        bars = await self.bars(tf, limit + WARMUP_BARS)
        return {"series": indicator_series(bars, limit), "summary": summary}

    async def timing(self) -> JSON:
        ctx = self._ctx
        now = ctx.clock.now()
        stored = await ctx.ops.kv_get(KV_ENGINE)
        engine = EngineState.from_json(stored) if stored else EngineState()
        return timing_view(
            now=now,
            settings=ctx.settings,
            active=engine.active(now),
            state=await self._engine_state(),
            outlook=await ctx.signals.latest_outlook(),
            events=await ctx.calendar.between(now - timedelta(hours=1), now + timedelta(days=2)),
            context=await self._context(),
            choice=await ctx.ops.kv_get(KV_TIMEFRAME),
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
        return dataclasses.asdict(perf) | {"desk": await self._desk(now)}

    async def _desk(self, now: datetime) -> JSON:
        """How the candidates the AI approved did against those it held back (signals/desk.py)."""
        stored = await self._ctx.ops.kv_get(KV_DESK) or {}
        reviews: list[JSON] = list(stored.get("reviews") or [])
        bars: list[Candle] = []
        if reviews:
            start = min(datetime.fromisoformat(r["at"]) for r in reviews)
            end = max(datetime.fromisoformat(r["valid_until"]) for r in reviews)
            source = reviews[-1].get("source") or await self._source()
            bars = await self._ctx.market.candles("M1", start, min(end, now), source=source)
        return compare_reviews(reviews, bars, now)

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
        context = await self._context()
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

"""Signal service: analyses + releases + market context + calendar -> SignalState / Signal, plus
the hourly outlook (11.3), the Monday-open outlook (11.6) and the weekly hourly-profile rebuild.

State that must survive a restart lives in kv_state: `signal_engine` (last signal, daily count,
last release), `trading_halt` (/stop) and `paper_daily` (realised R, written by the paper tracker).
"""

from __future__ import annotations

import asyncio
import bisect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import structlog

from gold_agent.analysis.llm_client import LlmClient, LlmFailed, LlmUnavailable
from gold_agent.analysis.prompts import load_prompt
from gold_agent.core.app import AppContext
from gold_agent.core.bus import (
    STREAM_ANALYSES,
    STREAM_CONTEXT,
    STREAM_EVENTS,
    STREAM_OUTLOOK,
    STREAM_RELEASES,
    STREAM_SIGNALS,
    STREAM_STATE,
    Handler,
    Message,
    StartAt,
    consume,
)
from gold_agent.econ_calendar.events import load_events
from gold_agent.models import (
    Analysis,
    CalendarEvent,
    LatencyStage,
    MarketContext,
    Outlook,
    ReleaseReading,
    Signal,
    SignalState,
)
from gold_agent.signals.engine import Decision, EngineState, SignalEngine, config_hash, wib_day
from gold_agent.signals.outlook import (
    OutlookBuilder,
    OutlookInputs,
    OutlookNarrative,
    floor_hour,
    narrative_prompt,
    weekend_notes,
)
from gold_agent.signals.profile import HourlyProfile, compute_profile
from gold_agent.signals.scoring import STALE_NEWS_HALF_LIVES
from gold_agent.timeutils.sessions import SessionClock, last_weekly

log = structlog.get_logger(__name__)

KV_ENGINE = "signal_engine"
KV_HALT = "trading_halt"
KV_PAPER_DAILY = "paper_daily"
KV_EVENT_MOVES = "event_moves"  # event study (Phase 6): event_key -> typical 60-min |move|

PRICE_KEEP = timedelta(hours=6)
PRICE_MAX_GAP = timedelta(minutes=5)
EVENTS_REFRESH = timedelta(minutes=5)
SLOW_REFRESH = timedelta(hours=1)  # learned reliability, event moves
PROFILE_REFRESH = timedelta(days=7)
TICK_SECONDS = 15.0
SCORE_CHANGE = 0.05  # publish the state again when |S| moves this much
WEEKEND_TRIGGER = "pembukaan Senin"
MOMENT_NAMES = {
    "tokyo_open": "Tokyo",
    "sge_open": "Shanghai",
    "london_open": "London",
    "ny_open": "New York",
}


class PriceHistory:
    """Recent mid prices so the engine can ask 'what was the price at t?'."""

    def __init__(self, keep: timedelta = PRICE_KEEP, max_gap: timedelta = PRICE_MAX_GAP) -> None:
        self._keep = keep
        self._max_gap = max_gap
        self._times: list[datetime] = []
        self._prices: list[float] = []

    def __len__(self) -> int:
        return len(self._times)

    def add(self, ts: datetime, price: float) -> None:
        if self._times and ts <= self._times[-1]:
            index = bisect.bisect_left(self._times, ts)
            if index < len(self._times) and self._times[index] == ts:
                self._prices[index] = price
                return
            self._times.insert(index, ts)
            self._prices.insert(index, price)
        else:
            self._times.append(ts)
            self._prices.append(price)
        cutoff = self._times[-1] - self._keep
        drop = bisect.bisect_left(self._times, cutoff)
        if drop:
            del self._times[:drop]
            del self._prices[:drop]

    def at(self, moment: datetime) -> float | None:
        """Last price at or before `moment`; None before the buffer or across a data gap."""
        index = bisect.bisect_right(self._times, moment) - 1
        if index < 0 or moment - self._times[index] > self._max_gap:
            return None
        return self._prices[index]


class SignalService:
    name = "signal"

    def __init__(self, ctx: AppContext, *, anthropic_client: Any | None = None) -> None:
        self._ctx = ctx
        settings = ctx.settings
        self.sessions = SessionClock(settings.sessions)
        self.catalog = load_events()
        self.engine = SignalEngine(settings, self.sessions, config_version=config_hash())
        self.outlooks = OutlookBuilder(settings, self.sessions, self.catalog)
        self.llm = LlmClient(
            settings,
            api_key=ctx.env.get("ANTHROPIC_API_KEY"),
            ops=ctx.ops,
            clock=ctx.clock,
            client=anthropic_client,
        )
        self.state = EngineState()
        self.context: MarketContext | None = None
        self.analyses: dict[str, Analysis] = {}
        self.prices = PriceHistory()
        self.last_state: SignalState | None = None
        self.profile = HourlyProfile(min_samples=settings.outlook.profile_min_samples)
        self.event_moves: dict[str, float] = {}
        self._published: SignalState | None = None
        self._published_at: datetime | None = None
        self._events: list[CalendarEvent] = []
        self._events_at: datetime | None = None
        self._learned: dict[str, float] = {}
        self._slow_at: datetime | None = None
        self._profile_at: datetime | None = None
        self._last_eval: datetime | None = None
        self._outlook_hour: datetime | None = None
        self._outlook_at: datetime | None = None
        self._weekend_done: datetime | None = None
        self._history_loaded = False
        self._lock = asyncio.Lock()
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
            "outlook.refresh": self._on_outlook_refresh,
            "signal.halt": self._on_halt,
            "signal.closed": self._on_closed,
            "paper.daily": self._on_paper_daily,
        }

    # ------------------------------------------------------------------ startup state

    async def load(self) -> None:
        ctx = self._ctx
        now = ctx.clock.now()
        stored = await ctx.ops.kv_get(KV_ENGINE)
        if stored:
            self.state = EngineState.from_json(stored)
        halt = await ctx.ops.kv_get(KV_HALT)
        self.state.halted = bool(halt and halt.get("halted"))
        await self._load_paper_daily()
        for analysis in await ctx.news.analyses_since(now - timedelta(hours=24)):
            self.analyses[analysis.cluster_id] = analysis
        self.last_state = await ctx.signals.latest_state()
        latest = await ctx.signals.latest_outlook()
        if latest is not None:  # a restart must not resend this hour's / weekend's outlook
            self._outlook_at = latest.created_at
            self._outlook_hour = floor_hour(latest.created_at)
        weekend = await ctx.signals.latest_outlook(trigger=WEEKEND_TRIGGER)
        if weekend is not None:
            self._weekend_done = last_weekly(
                ctx.settings.outlook.weekend_outlook, weekend.created_at
            )
        await self._refresh_slow(now)
        await self.refresh_profile(now)

    async def _load_paper_daily(self) -> None:
        daily = await self._ctx.ops.kv_get(KV_PAPER_DAILY)
        if daily:
            self.state.daily_r = float(daily.get("r") or 0.0)
            self.state.daily_r_day = daily.get("day")

    async def _save_state(self) -> None:
        await self._ctx.ops.kv_set(KV_ENGINE, self.state.to_json(), self._ctx.clock.now())

    async def _refresh_slow(self, now: datetime) -> None:
        self._learned = await self._ctx.ops.learned_reliability()
        moves = await self._ctx.ops.kv_get(KV_EVENT_MOVES) or {}
        self.event_moves = {str(k): float(v) for k, v in moves.items()}
        self._slow_at = now

    async def refresh_profile(self, now: datetime) -> None:
        """Load hourly_profile; rebuild it from stored H1 bars when it is a week old."""
        ctx = self._ctx
        stats, computed_at = await ctx.signals.profile()
        if computed_at is None or now - computed_at >= PROFILE_REFRESH:
            since = now - timedelta(days=ctx.settings.outlook.profile_days)
            sources = await ctx.market.candle_sources("H1", since)
            if sources:
                source = max(sources, key=lambda name: sources[name])
                candles = await ctx.market.candles("H1", since, now, source=source)
                fresh = compute_profile(candles)
                if fresh:
                    await ctx.signals.save_profile(fresh, now)
                    stats = fresh
                    log.info("profile.rebuilt", source=source, bars=len(candles))
        self.profile = HourlyProfile(stats, min_samples=ctx.settings.outlook.profile_min_samples)
        self._profile_at = now

    # ------------------------------------------------------------------ inputs

    def reliability(self, analysis: Analysis) -> float:
        learned = [self._learned[s] for s in analysis.source_ids if s in self._learned]
        return sum(learned) / len(learned) if learned else analysis.reliability

    def fresh_context(self, now: datetime) -> MarketContext | None:
        """The last snapshot, marked stale when the market service stopped publishing."""
        context = self.context
        if context is None:
            return None
        settings = self._ctx.settings
        max_age = max(
            3 * settings.infra.context_interval_seconds, settings.price_feed.stale_tick_seconds
        )
        age = (now - context.ts).total_seconds()
        if age > max_age and not context.stale:
            return context.model_copy(update={"stale": True, "tick_age_seconds": age})
        return context

    async def events(self, now: datetime) -> list[CalendarEvent]:
        if self._events_at is None or now - self._events_at >= EVENTS_REFRESH:
            self._events = await self._ctx.calendar.between(
                now - timedelta(hours=2), now + timedelta(hours=30)
            )
            self._events_at = now
        return self._events

    def _prune(self, now: datetime) -> None:
        for key, analysis in list(self.analyses.items()):
            limit = timedelta(minutes=STALE_NEWS_HALF_LIVES * analysis.half_life_minutes)
            if now - analysis.first_seen_at > max(limit, timedelta(hours=24)):
                del self.analyses[key]

    async def _seed_prices(self, context: MarketContext) -> None:
        """After a restart the price buffer is empty: seed it from stored M1 closes."""
        self._history_loaded = True
        if not context.source:
            return
        bars = await self._ctx.market.latest_candles("M1", 360, source=context.source)
        for bar in bars:
            self.prices.add(bar.close_time, bar.close)

    # ------------------------------------------------------------------ evaluation

    async def evaluate(self, *, force_publish: bool = False) -> Decision | None:
        async with self._lock:
            now = self._ctx.clock.now()
            context = self.fresh_context(now)
            if context is None:
                return None
            self._last_eval = now
            self._prune(now)
            decision = self.engine.evaluate(
                now=now,
                context=context,
                analyses=list(self.analyses.values()),
                events=await self.events(now),
                state=self.state,
                price_at=self.prices.at,
                reliability=self.reliability if self._learned else None,
            )
            if decision.signal is not None:
                await self._emit(decision.signal, decision.replaced)
            await self._publish_state(decision.state, now, force=force_publish)
            return decision

    async def _emit(self, signal: Signal, replaced: Signal | None) -> None:
        ctx = self._ctx
        if replaced is not None:
            self.state.closed_signal_ids.add(replaced.signal_id)
            await ctx.bus.publish(
                STREAM_EVENTS,
                {
                    "type": "signal.replaced",
                    "signal_id": replaced.signal_id,
                    "by": signal.signal_id,
                },
            )
        self.state.record_signal(signal)
        await self._save_state()
        await ctx.signals.save_signal(signal)
        await ctx.bus.publish(STREAM_SIGNALS, signal)
        log.info(
            "signal.created",
            signal=signal.signal_id,
            action=signal.action,
            score=signal.composite_score,
            confidence=signal.confidence,
        )
        if signal.first_seen_at is not None and signal.linked_news_ids:
            await ctx.ops.record_latency(
                LatencyStage(
                    ref_id=signal.linked_news_ids[0],
                    source_id=signal.trigger_source or "-",
                    stage="signal",
                    ts=signal.created_at_utc,
                    seconds_from_first_seen=(
                        signal.created_at_utc - signal.first_seen_at
                    ).total_seconds(),
                )
            )
        await ctx.bus.publish(
            STREAM_EVENTS,
            {
                "type": "outlook.refresh",
                "reason": f"sinyal {signal.action} baru",
                "ref_id": signal.signal_id,
            },
        )

    async def _publish_state(self, state: SignalState, now: datetime, *, force: bool) -> None:
        self.last_state = state
        previous = self._published
        changed = (
            previous is None
            or (previous.action, previous.active_signal_id, previous.wait_reasons, previous.halted)
            != (state.action, state.active_signal_id, state.wait_reasons, state.halted)
            or abs(previous.composite_score - state.composite_score) >= SCORE_CHANGE
        )
        interval = self._ctx.settings.signal_engine.publish_state_seconds
        due = self._published_at is None or (now - self._published_at).total_seconds() >= interval
        if not (changed or due or force):
            return
        self._published, self._published_at = state, now
        await self._ctx.bus.publish(STREAM_STATE, state)
        if changed or force:
            await self._ctx.signals.save_state(state)

    # ------------------------------------------------------------------ bus handlers

    async def handle_analysis(self, message: Message) -> None:
        analysis = Analysis.model_validate(message.data)
        self.analyses[analysis.cluster_id] = analysis
        await self.evaluate()

    async def handle_release(self, message: Message) -> None:
        release = ReleaseReading.model_validate(message.data)
        async with self._lock:
            self.state.last_release_at = release.first_seen_at
            self.state.last_release_name = release.event_name
            await self._save_state()
        await self.evaluate(force_publish=True)

    async def handle_context(self, message: Message) -> None:
        context = MarketContext.model_validate(message.data)
        self.context = context
        if not self._history_loaded:
            await self._seed_prices(context)
        if context.mid is not None:
            self.prices.add(context.ts, context.mid)
        now = self._ctx.clock.now()
        interval = self._ctx.settings.signal_engine.evaluate_seconds
        if self._last_eval is None or (now - self._last_eval).total_seconds() >= interval:
            await self.evaluate()

    async def handle_event(self, message: Message) -> None:
        handler = self._handlers.get(str(message.data.get("type")))
        if handler is not None:
            await handler(message.data)

    async def _on_outlook_refresh(self, data: dict[str, Any]) -> None:
        now = self._ctx.clock.now()
        debounce = timedelta(minutes=self._ctx.settings.outlook.refresh_debounce_minutes)
        if self._outlook_at is not None and now - self._outlook_at < debounce:
            return
        if self.sessions.is_weekend(now):
            if self._weekend_sent(now):  # weekend news updates the Monday-open outlook
                await self.build_outlook(f"{WEEKEND_TRIGGER} (update)", weekend=True)
            return
        await self.build_outlook(f"berita: {str(data.get('reason') or 'update')[:50]}")

    async def _on_halt(self, data: dict[str, Any]) -> None:
        now = self._ctx.clock.now()
        async with self._lock:
            self.state.halted = bool(data.get("halted"))
            await self._ctx.ops.kv_set(
                KV_HALT,
                {"halted": self.state.halted, "by": data.get("by"), "at": now.isoformat()},
                now,
            )
        log.warning("signal.halt", halted=self.state.halted, by=data.get("by"))
        await self.evaluate(force_publish=True)

    async def _on_closed(self, data: dict[str, Any]) -> None:
        signal_id = data.get("signal_id")
        if not signal_id:
            return
        async with self._lock:
            self.state.closed_signal_ids.add(str(signal_id))
            await self._save_state()
        await self.evaluate(force_publish=True)

    async def _on_paper_daily(self, data: dict[str, Any]) -> None:
        async with self._lock:
            self.state.daily_r = float(data.get("r") or 0.0)
            self.state.daily_r_day = data.get("day") or wib_day(self._ctx.clock.now())
        await self.evaluate(force_publish=True)

    # ------------------------------------------------------------------ outlook

    def _hourly_trigger(self, hour: datetime) -> str:
        """Session opens fall on the hour, so they name the hourly rebuild instead of adding one."""
        wanted = set(self._ctx.settings.outlook.refresh_on_moments)
        for moment_id, _ in self.sessions.moments_between(hour, hour + timedelta(minutes=59)):
            if moment_id in wanted:
                return f"pembukaan sesi {MOMENT_NAMES.get(moment_id, moment_id)}"
        return "hourly"

    async def build_outlook(self, trigger: str, *, weekend: bool = False) -> Outlook:
        ctx = self._ctx
        now = ctx.clock.now()
        start = self.sessions.next_weekly_open(now) if weekend else None
        first = start or now
        horizon = first + timedelta(days=6 if weekend else 1, hours=2)
        events = await ctx.calendar.between(now - timedelta(hours=1), horizon)
        playbooks = {p.event_id: p for p in await ctx.calendar.playbooks_between(now, horizon)}
        context = self.fresh_context(now)
        notes: list[str] = []
        if weekend:
            weekend_news = await ctx.news.analyses_since(self.sessions.last_weekly_close(now))
            notes = weekend_notes(
                now=now,
                sessions=self.sessions,
                context=context,
                analyses=weekend_news,
                events=events,
            )
        outlook = self.outlooks.build(
            OutlookInputs(
                now=now,
                trigger=trigger,
                context=context,
                state=self.last_state,
                active=self.state.active(now),
                events=events,
                playbooks=playbooks,
                profile=self.profile,
                event_moves=self.event_moves,
                start=start,
                notes=notes,
            )
        )
        outlook = await self._narrate(outlook)
        await ctx.signals.save_outlook(outlook)
        await ctx.bus.publish(STREAM_OUTLOOK, outlook)
        self._outlook_at = now
        log.info("outlook.built", trigger=trigger, narrative=outlook.narrative_id is not None)
        return outlook

    async def _narrate(self, outlook: Outlook) -> Outlook:
        if not self._ctx.settings.outlook.use_llm or not self.llm.available:
            return outlook
        recent = sorted(
            (a for a in self.analyses.values() if a.triage.is_gold_relevant),
            key=lambda a: (-a.triage.impact_score, -a.first_seen_at.timestamp()),
        )
        try:
            result = await self.llm.structured(
                "outlook",
                system=load_prompt("outlook").text,
                user=narrative_prompt(outlook, self.last_state, recent),
                schema=OutlookNarrative,
                ref_id=outlook.id,
            )
        except (LlmUnavailable, LlmFailed) as exc:
            note = f"narasi AI tidak tersedia ({exc})"
            return outlook.model_copy(update={"notes": [*outlook.notes, note]})
        return outlook.model_copy(
            update={"narrative_id": result.value.narrative_id, "model": result.model}
        )

    def _weekend_sent(self, now: datetime) -> bool:
        done = self._weekend_done
        return done is not None and done >= self.sessions.last_weekly_close(now)

    async def scheduled_outlooks(self, now: datetime) -> None:
        settings = self._ctx.settings.outlook
        weekend_at = last_weekly(settings.weekend_outlook, now)
        if (
            self.sessions.is_weekend(now)
            and weekend_at >= self.sessions.last_weekly_close(now)
            and self._weekend_done != weekend_at
        ):
            self._weekend_done = weekend_at
            await self.build_outlook(WEEKEND_TRIGGER, weekend=True)
            return
        hour = floor_hour(now)
        if (
            self._outlook_hour != hour
            and now.minute >= settings.refresh_minute
            and not self.sessions.is_weekend(now)
        ):
            self._outlook_hour = hour
            await self.build_outlook(self._hourly_trigger(hour))

    # ------------------------------------------------------------------ run

    async def tick(self) -> None:
        """Periodic work: keep the state fresh without context, rebuild outlooks and caches."""
        now = self._ctx.clock.now()
        interval = self._ctx.settings.signal_engine.evaluate_seconds
        if self._last_eval is None or (now - self._last_eval).total_seconds() >= interval:
            await self.evaluate()
        if self._slow_at is None or now - self._slow_at >= SLOW_REFRESH:
            await self._refresh_slow(now)
            await self._load_paper_daily()
        if self._profile_at is None or now - self._profile_at >= PROFILE_REFRESH:
            await self.refresh_profile(now)
        await self.scheduled_outlooks(now)

    async def _tick_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("signal.tick_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            except TimeoutError:
                continue

    async def run(self, stop: asyncio.Event) -> None:
        await self.load()
        bus = self._ctx.bus
        consumers: list[tuple[str, str, Handler, StartAt]] = [
            (STREAM_ANALYSES, "signal", self.handle_analysis, "earliest"),
            (STREAM_RELEASES, "signal-releases", self.handle_release, "earliest"),
            (STREAM_CONTEXT, "signal-context", self.handle_context, "latest"),
            (STREAM_EVENTS, "signal-events", self.handle_event, "latest"),
        ]
        tasks = [
            asyncio.create_task(consume(bus, stream, group, "signal-1", handler, stop, start=start))
            for stream, group, handler, start in consumers
        ]
        tasks.append(asyncio.create_task(self._tick_loop(stop)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()

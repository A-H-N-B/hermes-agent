"""Signal service: analyses + releases + market context + calendar -> SignalState / Signal, plus
the hourly outlook (11.3), the Monday-open outlook (11.6) and the weekly hourly-profile rebuild.

Every evaluation first picks the chart to trade on (signals/timeframe.py, fed by the measured
accuracy in signals/accuracy.py). A candidate the engine produces then goes to Opus 5.5 for the
final BUY / SELL / WAIT and timeframe (signals/desk.py); the review runs in the background and the
next evaluation, on fresh prices, applies it. Without the AI the engine decides alone, labelled.

State that must survive a restart lives in kv_state: `signal_engine` (last signal, daily count,
last release), `trading_halt` (/stop) and `paper_daily` (realised R, written by the paper tracker).
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import structlog

from gold_agent.analysis.drivers import DriverKB
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
from gold_agent.market.series import PriceSeries
from gold_agent.models import (
    Analysis,
    CalendarEvent,
    LatencyStage,
    MarketContext,
    Outlook,
    ReleaseReading,
    Signal,
    SignalReview,
    SignalState,
)
from gold_agent.signals.accuracy import AccuracyBook
from gold_agent.signals.desk import (
    KEEP_REVIEWS,
    KV_DESK,
    Desk,
    DeskDecision,
    Verdict,
    desk_brief,
    tally,
    verify,
)
from gold_agent.signals.engine import (
    KV_ENGINE,
    KV_EVENT_MOVES,
    KV_HALT,
    KV_PAPER_DAILY,
    Decision,
    EngineState,
    SignalEngine,
    config_hash,
    wib_day,
)
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
from gold_agent.signals.timeframe import KV_TIMEFRAME, TfChoice, choose_timeframe
from gold_agent.timeutils.sessions import SessionClock, last_weekly

log = structlog.get_logger(__name__)


EVENTS_REFRESH = timedelta(minutes=5)
SLOW_REFRESH = timedelta(hours=1)  # learned reliability, event moves
PROFILE_REFRESH = timedelta(days=7)
TICK_SECONDS = 15.0
SCORE_CHANGE = 0.05  # publish the state again when |S| moves this much
WEEKEND_TRIGGER = "pembukaan Senin"
PENDING_REVIEW = "menunggu keputusan AI (Opus 5.5) atas kandidat ini"
CHOICE_SAVE = timedelta(seconds=60)  # the dashboard's copy of the timeframe table
MOMENT_NAMES = {
    "tokyo_open": "Tokyo",
    "sge_open": "Shanghai",
    "london_open": "London",
    "ny_open": "New York",
}


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
        self.accuracy = AccuracyBook(ctx)
        self.desk = Desk(settings.llm.desk_reviews_per_day)
        self.desk_prompt = load_prompt("desk", DriverKB.from_file())
        self.choice: TfChoice | None = None
        self._choice_saved: tuple[datetime, tuple[Any, ...]] | None = None
        self.review_task: asyncio.Task[None] | None = None  # Opus deciding a candidate
        self.state = EngineState()
        self.context: MarketContext | None = None
        self.analyses: dict[str, Analysis] = {}
        self.prices = PriceSeries(keep=timedelta(hours=6))
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
            events = await self.events(now)
            choice = await self._choose_timeframe(now, context)

            def run(timeframe: TfChoice) -> Decision:
                return self.engine.evaluate(
                    now=now,
                    context=context,
                    analyses=list(self.analyses.values()),
                    events=events,
                    state=self.state,
                    price_at=self.prices.at,
                    reliability=self.reliability if self._learned else None,
                    timeframe=timeframe,
                )

            decision = run(choice)
            if decision.signal is not None:
                decision = await self._decide(
                    decision, decision.signal, choice, run, now, context, events
                )
            if decision.signal is not None:
                await self._emit(decision.signal, decision.replaced)
            await self._publish_state(decision.state, now, force=force_publish)
            return decision

    # ------------------------------------------------------------------ timeframe + AI decision

    async def _choose_timeframe(self, now: datetime, context: MarketContext) -> TfChoice:
        table = await self.accuracy.table(source=context.source)
        choice = choose_timeframe(
            context=context,
            session=self.sessions.session(now),
            settings=self._ctx.settings,
            accuracy=table,
            current=self.choice.tf if self.choice else None,
        )
        self.choice = choice
        summary = (choice.tf, choice.basis, choice.reason, tuple(choice.eligible))
        saved = self._choice_saved
        if saved is None or saved[1] != summary or now - saved[0] >= CHOICE_SAVE:
            self._choice_saved = (now, summary)
            await self._ctx.ops.kv_set(KV_TIMEFRAME, choice.to_json(), now)
        return choice

    def _hold(self, decision: Decision, now: datetime, reason: str) -> Decision:
        """The candidate is not sent (yet): publish WAIT with the reason instead."""
        active = self.state.active(now)
        state = decision.state.model_copy(
            update={
                "action": "WAIT",
                "active_signal_id": active.signal_id if active else None,
                "wait_reasons": [reason],
            }
        )
        return Decision(state=state, breakdown=decision.breakdown)

    @staticmethod
    def _reviewed(
        decision: Decision, signal: Signal, review: SignalReview, reason: str | None = None
    ) -> Decision:
        update: dict[str, Any] = {"review": review}
        if reason is not None:
            update["timeframe_reason"] = reason
        return dataclasses.replace(decision, signal=signal.model_copy(update=update))

    async def _decide(
        self,
        decision: Decision,
        candidate: Signal,
        choice: TfChoice,
        run: Callable[[TfChoice], Decision],
        now: datetime,
        context: MarketContext,
        events: list[CalendarEvent],
    ) -> Decision:
        """Opus's final say on the engine's candidate (signals/desk.py)."""
        verdict = self.desk.standing(candidate, now)
        if verdict is None:
            if self.desk.busy:
                return self._hold(decision, now, PENDING_REVIEW)
            why = self._cannot_review(now)
            if why is None:
                self._start_review(decision, candidate, choice, context, events, now)
                return self._hold(decision, now, PENDING_REVIEW)
            verdict = Verdict(
                side=candidate.action,
                decision="UNAVAILABLE",
                at=now,
                composite=candidate.composite_score,
                note=why,
            )
            self.desk.last = verdict
        if verdict.decision == "UNAVAILABLE":
            return self._reviewed(decision, candidate, verdict.review())
        if not verdict.approved:
            reason = verdict.reasons[0] if verdict.reasons else "tanpa alasan"
            held = f"AI (Opus 5.5) menahan {candidate.action}{verdict.count}: {reason}"
            return self._hold(decision, now, held)
        self.desk.last = None  # an approval answers this one candidate
        tf = verdict.timeframe or choice.tf
        if tf != choice.tf:
            picked = TfChoice(tf, "opus", f"{tf} dipilih Opus 5.5", choice.session, choice.options)
            redo = run(picked)
            if redo.signal is not None and redo.signal.action == candidate.action:
                return self._reviewed(redo, redo.signal, verdict.review())
            note = f"chart {tf} pilihan AI belum siap entry, dipakai {choice.tf}"
            return self._reviewed(decision, candidate, verdict.review(note=note))
        reason = f"{choice.reason}; disetujui Opus 5.5"
        return self._reviewed(decision, candidate, verdict.review(), reason)

    def _cannot_review(self, now: datetime) -> str | None:
        if not self.llm.available:
            return "AI tidak aktif (rules-only, tanpa API key, atau Claude sedang bermasalah)"
        if not self.desk.take(wib_day(now)):
            cap = self._ctx.settings.llm.desk_reviews_per_day
            return f"batas {cap} review AI per hari tercapai"
        return None

    def _start_review(
        self,
        decision: Decision,
        candidate: Signal,
        choice: TfChoice,
        context: MarketContext,
        events: list[CalendarEvent],
        now: datetime,
    ) -> None:
        brief = desk_brief(
            candidate=candidate,
            choice=choice,
            breakdown=decision.breakdown,
            context=context,
            analyses=list(self.analyses.values()),
            events=events,
            active=self.state.active(now),
            settings=self._ctx.settings,
            now=now,
        )
        self.desk.busy = True
        self.review_task = asyncio.create_task(self._ask_desk(candidate, choice, brief))

    async def _ask_desk(self, candidate: Signal, choice: TfChoice, brief: str) -> None:
        try:
            verdict = await self._consult(candidate, choice, brief)
        finally:
            self.desk.busy = False
        self.desk.last = verdict
        log.info(
            "desk.verdict",
            candidate=candidate.signal_id,
            decision=verdict.decision,
            timeframe=verdict.timeframe,
        )
        if verdict.decision != "UNAVAILABLE":
            await self._log_review(verdict, candidate)
        await self.evaluate(force_publish=True)

    async def _consult(self, candidate: Signal, choice: TfChoice, brief: str) -> Verdict:
        """`llm.desk_votes` independent answers in parallel, counted by `tally`."""
        asked = self._ctx.settings.llm.desk_votes
        answers = await asyncio.gather(
            *(self._vote(candidate, choice, brief) for _ in range(asked))
        )
        votes = [answer for answer in answers if isinstance(answer, Verdict)]
        failures = [answer for answer in answers if isinstance(answer, str)]
        now = self._ctx.clock.now()
        if not votes:
            return Verdict(
                side=candidate.action,
                decision="UNAVAILABLE",
                at=now,
                composite=candidate.composite_score,
                note=failures[0],
            )
        return tally(votes, failures, candidate=candidate, choice=choice, at=now)

    async def _vote(self, candidate: Signal, choice: TfChoice, brief: str) -> Verdict | str:
        """One answer from Opus through the guardrails, or why there is none."""
        try:
            result = await self.llm.structured(
                "desk",
                system=self.desk_prompt.text,
                user=brief,
                schema=DeskDecision,
                ref_id=candidate.signal_id,
                priority=True,  # the decision itself: may use the budget kept for it
            )
        except LlmUnavailable as exc:
            return f"AI tidak dipakai: {exc}"
        except LlmFailed as exc:
            return f"AI gagal memutuskan ({exc})"
        return verify(
            result.value,
            candidate=candidate,
            choice=choice,
            model=result.model,
            at=self._ctx.clock.now(),
        )

    async def _log_review(self, verdict: Verdict, candidate: Signal) -> None:
        ops = self._ctx.ops
        stored = await ops.kv_get(KV_DESK) or {}
        entries: list[dict[str, Any]] = list(stored.get("reviews") or [])
        entries.append(
            verdict.to_json()
            | {
                "candidate_tf": candidate.timeframe,
                "entry_low": candidate.entry_low,
                "entry_high": candidate.entry_high,
                "stop_loss": candidate.stop_loss,
                "tp1": candidate.tp1,
                "valid_until": candidate.valid_until_utc.isoformat(),
                "source": candidate.price_source,
            }
        )
        await ops.kv_set(KV_DESK, {"reviews": entries[-KEEP_REVIEWS:]}, self._ctx.clock.now())

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
        while not await self._ctx.clock.wait(stop, TICK_SECONDS):
            try:
                await self.tick()
            except Exception:
                log.exception("signal.tick_failed")

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
            if self.review_task is not None:
                self.review_task.cancel()

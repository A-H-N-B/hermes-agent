"""Tanya AI: the user's questions ("kapan aku harus BUY atau SELL?") answered from everything the
agent knows right now (dashboard chat, Telegram /tanya or a plain message).

The program writes the facts into one brief: the price (and whether it was re-based onto the
broker's), the engine's state and its reasons, the active signal with its levels and odds, a
momentum impulse under way, when to click (the timing view: blackout around the next release,
the outlook hours leaning BUY or SELL, the price levels that would confirm them), the charts,
upcoming events, recent news, related markets and the day's fundamentals. Opus 5.5 answers from
that brief in plain Indonesian, citing only its numbers (prompts/chat_system.md). Without the AI
(disabled, outage, budget, the daily cap) the answer is the program's own summary of the same
facts, so a question always gets an answer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from gold_agent.analysis.drivers import DriverKB
from gold_agent.analysis.llm_client import (
    LlmClient,
    LlmFailed,
    LlmUnavailable,
    Turn,
    wib_day_start,
)
from gold_agent.analysis.prompts import load_prompt, untrusted
from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.dashboard.timing import timing_view
from gold_agent.fundamentals.snapshot import KV_FUNDAMENTALS
from gold_agent.market.impulse import describe
from gold_agent.models import (
    Analysis,
    CalendarEvent,
    FundamentalSnapshot,
    MarketContext,
    Outlook,
    Signal,
    SignalState,
)
from gold_agent.signals.desk import model_name
from gold_agent.signals.engine import KV_ENGINE, EngineState
from gold_agent.signals.timeframe import KV_TIMEFRAME
from gold_agent.storage.ops_repo import OpsRepo
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, fmt_wib

JSON = dict[str, Any]
MAX_QUESTION = 1000  # characters of one question
MAX_TURNS = 6  # earlier messages sent along (three questions and their answers)
MAX_TURN_CHARS = 1500  # of each earlier message
NEWS_HOURS = 6
EVENT_HOURS = 24
CHART_TFS = ("M5", "M15", "H1", "H4")
TREND_ID = {"up": "naik", "down": "turun", "flat": "datar"}
BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
HEADING = re.compile(r"^#{1,6}\s+", re.M)
STAR_BULLET = re.compile(r"^(\s*)\*\s+", re.M)


@dataclass(frozen=True)
class ChatFacts:
    now: datetime
    context: MarketContext | None
    state: SignalState | None
    active: Signal | None
    outlook: Outlook | None
    events: list[CalendarEvent]
    analyses: list[Analysis]
    fundamentals: FundamentalSnapshot | None
    choice: JSON | None  # the signal service's last timeframe choice


@dataclass(frozen=True)
class ChatAnswer:
    text: str
    model: str | None  # None: the program's own summary (no AI)
    note: str | None = None  # why there was no AI answer


async def gather_facts(
    ctx: AppContext, *, context: MarketContext | None, state: SignalState | None
) -> ChatFacts:
    """Everything stored, around the freshest context and state the caller holds (the
    dashboard and the notifier keep the ones pushed every second)."""
    now = ctx.clock.now()
    stored = await ctx.ops.kv_get(KV_ENGINE)
    engine = EngineState.from_json(stored) if stored else EngineState()
    fundamentals = await ctx.ops.kv_get(KV_FUNDAMENTALS)
    return ChatFacts(
        now=now,
        context=context or await ctx.market.latest_context(),
        state=state or await ctx.signals.latest_state(),
        active=engine.active(now),
        outlook=await ctx.signals.latest_outlook(),
        events=await ctx.calendar.between(
            now - timedelta(hours=1), now + timedelta(hours=EVENT_HOURS)
        ),
        analyses=await ctx.news.analyses_since(now - timedelta(hours=NEWS_HOURS)),
        fundamentals=FundamentalSnapshot.model_validate(fundamentals) if fundamentals else None,
        choice=await ctx.ops.kv_get(KV_TIMEFRAME),
    )


# ------------------------------------------------------------------ the brief


def _num(value: float | None, pattern: str = "{:.2f}") -> str:
    return "-" if value is None else pattern.format(value)


def _price_lines(context: MarketContext | None) -> list[str]:
    if context is None or context.mid is None:
        return ["<harga>data harga tidak tersedia</harga>"]
    source = context.source or "-"
    if context.proxy and context.price_offset:
        source += (
            f", proxy yang sudah disamakan ke harga broker (selisih {context.price_offset:+.2f})"
        )
    elif context.proxy:
        source += ", proxy (token emas), belum disamakan ke harga broker: beda beberapa dolar"
    stale = " · BASI (tidak ada tick baru)" if context.stale else ""
    return [
        f"<harga>bid {_num(context.bid)} / ask {_num(context.ask)} (mid {_num(context.mid)}) · "
        f"sumber {source} · spread {_num(context.spread)}{stale}</harga>"
    ]


def _engine_lines(state: SignalState | None, settings: Settings) -> list[str]:
    if state is None:
        return ["<mesin>status mesin belum tersedia</mesin>"]
    c = state.components
    lines = [
        f'<mesin aksi="{state.action}" skor="{state.composite_score:+.2f}" '
        f'ambang="±{settings.signal_engine.entry_threshold:.2f}" '
        f'confidence="{state.confidence}" chart="{state.timeframe or "-"}">',
        f"komponen: berita {c.N:+.2f} · makro {c.M:+.2f} · teknikal {c.T:+.2f} · harga {c.P:+.2f}",
    ]
    if state.wait_reasons:
        lines.append("menunggu karena: " + "; ".join(state.wait_reasons))
    if state.reasons:
        lines.append("faktor: " + "; ".join(state.reasons))
    lines.append("</mesin>")
    return lines


def _active_lines(active: Signal | None) -> list[str]:
    if active is None:
        return ["<sinyal_aktif>tidak ada</sinyal_aktif>"]
    parts = [
        f"{active.action} di chart {active.timeframe or '-'} sejak "
        f"{fmt_wib(active.created_at_utc)} WIB (setup {active.setup_type})",
        f"entry {active.entry_low:.2f}–{active.entry_high:.2f}, SL {active.stop_loss:.2f}, "
        f"TP1 {active.tp1:.2f}, TP2 {active.tp2:.2f}, TP3 {active.tp3:.2f}",
        f"berlaku s/d {active.valid_until_wib}",
        f"confidence {active.confidence}/100",
    ]
    if active.odds is not None:
        parts.append(f"peluang: {active.odds.note}")
    review = active.review
    if review is not None and review.model:
        reason = review.reasons[0] if review.reasons else "-"
        parts.append(f"disetujui AI ({model_name(review.model)}): {reason}")
    if active.impulse_note:
        parts.append(f"momentum: {active.impulse_note}")
    return ["<sinyal_aktif>" + " · ".join(parts) + "</sinyal_aktif>"]


def _impulse_lines(context: MarketContext | None) -> list[str]:
    impulse = context.impulse if context else None
    if impulse is None:
        return ["<impuls>tidak ada gerakan luar biasa saat ini</impuls>"]
    return [f"<impuls>{describe(impulse)}; sejak {fmt_wib(impulse.since)} WIB</impuls>"]


def _timing_lines(facts: ChatFacts, settings: Settings) -> list[str]:
    view = timing_view(
        now=facts.now,
        settings=settings,
        active=facts.active,
        state=facts.state,
        outlook=facts.outlook,
        events=facts.events,
        context=facts.context,
        choice=facts.choice,
    )
    lines = ["<kapan>"]
    blackout = view.get("blackout")
    if blackout:
        lines.append(
            f"- jangan trading {blackout['from_wib']}–{blackout['to_wib']} WIB: rilis "
            f"{untrusted(blackout['title'], 80)} pukul {blackout['at_wib']} WIB"
        )
    for window in view.get("windows") or []:
        confidence = window.get("confidence")
        lines.append(
            f"- jam {window['label_wib']} WIB condong {window['action']} (confidence "
            f"{confidence if confidence is not None else '-'}"
            f"{', bersyarat' if window.get('conditional') else ''}): "
            f"{untrusted(window.get('scenario'), 200)}"
        )
    triggers = view.get("triggers")
    if triggers:
        above, below = triggers.get("buy_above"), triggers.get("sell_below")
        if above:
            lines.append(f"- level atas terdekat {above['price']:.2f} ({above['name']})")
        if below:
            lines.append(f"- level bawah terdekat {below['price']:.2f} ({below['name']})")
    if len(lines) == 1:
        lines.append("- outlook belum condong BUY/SELL dan tidak ada rilis besar dekat")
    lines.append("</kapan>")
    return lines


def _chart_lines(context: MarketContext | None) -> list[str]:
    if context is None:
        return []
    lines = ["<chart>"]
    for tf in CHART_TFS:
        state = context.timeframes.get(tf)
        if state is None or state.close is None:
            continue
        gap = None
        if state.ema20 is not None and state.atr14:
            gap = (state.close - state.ema20) / state.atr14
        lines.append(
            f"- {tf}: tren {TREND_ID.get(state.trend or '', '-')}, "
            f"RSI {_num(state.rsi14, '{:.0f}')}, ATR {_num(state.atr14)}, "
            f"harga-EMA20 {_num(gap, '{:+.1f}')} ATR, "
            f"tren Kalman t {_num(state.kalman_t, '{:+.1f}')}, rezim {state.regime or '-'}"
        )
    lines.append("</chart>")
    return lines if len(lines) > 2 else []


def _event_lines(events: Sequence[CalendarEvent], now: datetime) -> list[str]:
    upcoming = [
        e
        for e in sorted(events, key=lambda e: e.scheduled_at)
        if e.scheduled_at >= now and e.impact in ("high", "medium") and not e.all_day
    ][:8]
    lines = ["<event>"]
    lines += [
        f"- {fmt_wib(e.scheduled_at, with_date=True)} WIB {e.country} {untrusted(e.title, 80)} "
        f"[{e.impact}]" + (f" forecast {untrusted(e.forecast, 20)}" if e.forecast else "")
        for e in upcoming
    ] or [f"- tidak ada rilis penting dalam {EVENT_HOURS} jam"]
    lines.append("</event>")
    return lines


def _news_lines(analyses: Sequence[Analysis]) -> list[str]:
    recent = sorted(
        (a for a in analyses if a.triage.is_gold_relevant),
        key=lambda a: a.first_seen_at,
        reverse=True,
    )[:6]
    lines = ["<berita>"]
    for analysis in recent:
        triage = analysis.triage
        status = "belum terkonfirmasi" if triage.needs_confirmation else "terkonfirmasi"
        lines.append(
            f"- [{fmt_wib(analysis.first_seen_at)} WIB, dampak {triage.impact_score}/5, "
            f"{triage.direction_gold}, {status}] "
            + untrusted(triage.summary_id or analysis.title, 240)
        )
    if not recent:
        lines.append(f"- tidak ada berita relevan dalam {NEWS_HOURS} jam terakhir")
    lines.append("</berita>")
    return lines


def _market_lines(
    context: MarketContext | None, fundamentals: FundamentalSnapshot | None
) -> list[str]:
    lines = ["<pasar_terkait>"]
    for asset in context.cross.values() if context else []:
        lines.append(
            f"- {asset.label}: 15m {_num(asset.change_15m_pct, '{:+.2f}')}%, 1j "
            f"{_num(asset.change_1h_pct, '{:+.2f}')}% · tarikan ke emas "
            f"{_num(asset.effect, '{:+.2f}')}"
        )
    if context is not None and not context.cross and context.dxy_z_1h is not None:
        lines.append(f"- dolar ({context.dxy_source or 'proxy'}): z 1 jam {context.dxy_z_1h:+.1f}")
    if len(lines) == 1:
        lines.append("- data pasar terkait tidak tersedia")
    lines.append("</pasar_terkait>")
    summary = fundamentals.summary if fundamentals else "data fundamental harian tidak tersedia"
    lines.append(f"<fundamental>{untrusted(summary, 400)}</fundamental>")
    return lines


def chat_brief(facts: ChatFacts, settings: Settings) -> str:
    """The facts for the AI, as tagged text (news text neutralised: it is data)."""
    context = facts.context
    session = SESSION_LABEL_ID.get(context.session, context.session) if context else "-"
    market = "-" if context is None else "buka" if context.market_open else "tutup"
    return "\n".join(
        [
            f"<waktu>{fmt_wib(facts.now, with_date=True)} WIB · sesi {session} · "
            f"pasar {market}</waktu>",
            *_price_lines(context),
            *_engine_lines(facts.state, settings),
            *_active_lines(facts.active),
            *_impulse_lines(context),
            *_timing_lines(facts, settings),
            *_chart_lines(context),
            *_event_lines(facts.events, facts.now),
            *_news_lines(facts.analyses),
            *_market_lines(context, facts.fundamentals),
        ]
    )


def summary(facts: ChatFacts) -> str:
    """The program's own answer, without AI: what to do now and what to watch."""
    lines: list[str] = []
    active, state, context = facts.active, facts.state, facts.context
    if active is not None:
        lines.append(
            f"Sinyal aktif: {active.action} (chart {active.timeframe or '-'}), entry "
            f"{active.entry_low:.2f}–{active.entry_high:.2f}, SL {active.stop_loss:.2f}, TP1 "
            f"{active.tp1:.2f}, TP2 {active.tp2:.2f}, berlaku s/d {active.valid_until_wib}."
        )
    elif state is not None:
        why = "; ".join(state.wait_reasons[:2]) or "belum ada alasan tercatat"
        lines.append(f"Sekarang TUNGGU (skor {state.composite_score:+.2f}): {why}.")
    else:
        lines.append("Status mesin belum tersedia.")
    if context is not None and context.impulse is not None:
        lines.append(f"Saat ini ada {describe(context.impulse)}.")
    if context is not None and context.mid is not None:
        lines.append(f"Harga {context.mid:.2f} ({context.source or '-'}).")
    lines.append("Bukan saran keuangan. Keputusan & risiko di tanganmu.")
    return " ".join(lines)


# ------------------------------------------------------------------ the assistant


def plain(text: str) -> str:
    """Answers are shown as they are (Telegram HTML, dashboard text), so markdown the model still
    writes despite the prompt loses its markers, never its words."""
    text = BOLD.sub(r"\1", text)
    text = HEADING.sub("", text)
    return STAR_BULLET.sub(r"\1- ", text)


def _turns(history: Sequence[Turn], brief: str, question: str) -> list[Turn]:
    """Earlier messages (trimmed), then the facts and the question as the last user turn. The
    conversation must alternate and start with the user."""
    earlier: list[Turn] = [
        (role, text[:MAX_TURN_CHARS]) for role, text in history[-MAX_TURNS:] if text
    ]
    while earlier and earlier[0][0] != "user":
        earlier = earlier[1:]
    clean: list[Turn] = []
    for turn in earlier:
        if clean and clean[-1][0] == turn[0]:
            continue
        clean.append(turn)
    if clean and clean[-1][0] == "user":
        clean = clean[:-1]
    question = question.strip()[:MAX_QUESTION]
    return [
        *clean,
        ("user", f"{brief}\n\n<pertanyaan>{untrusted(question, MAX_QUESTION)}</pertanyaan>"),
    ]


class ChatAssistant:
    """Answers questions; at most `llm.chat_per_day` go to the AI per WIB day, counted in the
    usage log, so the dashboard and Telegram share them and a restart does not reset them."""

    def __init__(self, settings: Settings, llm: LlmClient, ops: OpsRepo) -> None:
        self._settings = settings
        self._llm = llm
        self._ops = ops
        self._prompt = load_prompt("chat", DriverKB.from_file())

    async def _asked_today(self, now: datetime) -> int:
        rows = await self._ops.llm_usage_summary(wib_day_start(now))
        return sum(row["calls"] for row in rows if row["route"] == "chat")

    async def answer(self, question: str, history: Sequence[Turn], facts: ChatFacts) -> ChatAnswer:
        if not self._llm.available:
            return ChatAnswer(
                summary(facts), None, "AI tidak aktif (tanpa API key atau sedang bermasalah)"
            )
        cap = self._settings.llm.chat_per_day
        if await self._asked_today(facts.now) >= cap:
            return ChatAnswer(summary(facts), None, f"batas {cap} pertanyaan AI per hari tercapai")
        brief = chat_brief(facts, self._settings)
        try:
            result = await self._llm.text(
                "chat", system=self._prompt.text, turns=_turns(history, brief, question)
            )
        except (LlmUnavailable, LlmFailed) as exc:
            return ChatAnswer(summary(facts), None, f"AI tidak menjawab: {exc}")
        return ChatAnswer(plain(result.value), result.model)

"""Opus 5.5's final say on every signal: BUY / SELL / WAIT, and the chart to trade it on.

The engine proposes a candidate: a direction from news + macro + technical + price, every hard
filter passed, levels computed. Opus reviews the whole picture in one structured call and decides.
Guardrails keep that safe:

* it may approve the engine's direction or WAIT, never flip it: an opposite call is held as WAIT,
  since no evidence the system computed produced that trade;
* its timeframe must be one the selector found usable (data present, spread small next to SL);
* prices, SL and TP stay the engine's, recomputed on the chosen chart, never the model's.

A WAIT stands until something material changes (cooldown, a score move, a new side), so a held
candidate is not re-sent every evaluation. Every decision is logged (kv `desk_reviews`) for the
Performa page, which compares how approved and held-back candidates would have done.
"""

from __future__ import annotations

import bisect
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from gold_agent.analysis.prompts import untrusted
from gold_agent.config import Settings
from gold_agent.models import (
    Analysis,
    CalendarEvent,
    Candle,
    MarketContext,
    Signal,
    SignalReview,
)
from gold_agent.signals.engine import TF_MINUTES, entry_timing
from gold_agent.signals.forecast_score import wilson
from gold_agent.signals.levels import Side, compute_levels
from gold_agent.signals.scoring import ScoreBreakdown
from gold_agent.signals.timeframe import RANK, TfChoice, TfOption
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, fmt_wib

JSON = dict[str, Any]
KV_DESK = "desk_reviews"  # {"reviews": [...]}, newest last, for the Performa comparison
KEEP_REVIEWS = 300
COOLDOWN = timedelta(minutes=10)  # a WAIT stands this long ...
RESCORE = 0.10  # ... unless the composite score moves this much
APPROVAL_TTL = timedelta(minutes=2)  # an approval answers the candidate it was asked about
UNAVAILABLE_TTL = timedelta(minutes=5)  # then try the AI again
TREND_ID = {"up": "naik", "down": "turun", "flat": "datar"}

Reason = Annotated[str, Field(max_length=300)]


class DeskDecision(BaseModel):
    decision: Literal["BUY", "SELL", "WAIT"]
    timeframe: Literal["M1", "M5", "M15", "M30", "H1"]
    confidence: int = Field(ge=0, le=100)
    reasons_id: list[Reason] = Field(min_length=1, max_length=4)
    caution_id: str | None = Field(default=None, max_length=400)


@dataclass(frozen=True)
class Verdict:
    side: str  # the candidate's direction
    decision: Literal["BUY", "SELL", "WAIT", "UNAVAILABLE"]
    at: datetime
    composite: float
    timeframe: str | None = None
    confidence: int | None = None
    reasons: tuple[str, ...] = ()
    caution: str | None = None
    note: str | None = None  # why the AI did not decide
    model: str | None = None

    @property
    def approved(self) -> bool:
        return self.decision == self.side

    def review(self, note: str | None = None) -> SignalReview:
        return SignalReview(
            model=self.model if self.decision != "UNAVAILABLE" else None,
            confidence=self.confidence,
            timeframe=self.timeframe,
            reasons=list(self.reasons),
            caution=self.caution,
            note=note or self.note,
        )

    def to_json(self) -> JSON:
        return {
            "side": self.side,
            "decision": self.decision,
            "at": self.at.isoformat(),
            "composite": self.composite,
            "timeframe": self.timeframe,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "caution": self.caution,
            "note": self.note,
            "model": self.model,
        }


def verify(
    value: DeskDecision,
    *,
    candidate: Signal,
    choice: TfChoice,
    model: str,
    at: datetime,
) -> Verdict:
    """Apply the guardrails to what Opus answered."""
    reasons = tuple(text.strip() for text in value.reasons_id if text.strip())
    decision: Literal["BUY", "SELL", "WAIT"] = value.decision
    timeframe = value.timeframe if value.timeframe in choice.eligible else choice.tf
    if decision not in (candidate.action, "WAIT"):
        why = reasons[0] if reasons else "arah berlawanan dengan kandidat"
        decision = "WAIT"
        reasons = (f"AI tidak setuju dengan {candidate.action}: {why}", *reasons[1:])
    return Verdict(
        side=candidate.action,
        decision=decision,
        at=at,
        composite=candidate.composite_score,
        timeframe=timeframe,
        confidence=value.confidence,
        reasons=reasons or ("tanpa alasan",),
        caution=value.caution_id,
        model=model,
    )


class Desk:
    """What the service remembers between evaluations: the last verdict, the review running,
    and the per-WIB-day count (capped by llm.desk_reviews_per_day)."""

    def __init__(self, reviews_per_day: int) -> None:
        self._cap = reviews_per_day
        self.last: Verdict | None = None
        self.busy = False
        self._day: str | None = None
        self._count = 0

    def standing(self, candidate: Signal, now: datetime) -> Verdict | None:
        """The verdict that still answers this candidate, or None (time to ask again)."""
        verdict = self.last
        if verdict is None or verdict.side != candidate.action:
            return None
        age = now - verdict.at
        if verdict.decision == "UNAVAILABLE":
            return verdict if age < UNAVAILABLE_TTL else None
        if verdict.approved:
            return verdict if age < APPROVAL_TTL else None
        moved = abs(candidate.composite_score - verdict.composite) >= RESCORE
        return None if age >= COOLDOWN or moved else verdict

    def take(self, day: str) -> bool:
        """Count one review for the WIB day; False once the daily cap is reached."""
        if self._day != day:
            self._day, self._count = day, 0
        if self._count >= self._cap:
            return False
        self._count += 1
        return True


# ------------------------------------------------------------------ evidence


Touch = Literal["tp1", "sl", "expired"]


def first_touch(side: str, stop: float, target: float, bars: Sequence[Candle]) -> Touch:
    """What price reached first after a candidate: TP1 or the stop (a bar touching both counts as
    the stop, the cautious reading), or neither before the candidate's validity ran out."""
    for bar in bars:
        if (bar.low <= stop) if side == "BUY" else (bar.high >= stop):
            return "sl"
        if (bar.high >= target) if side == "BUY" else (bar.low <= target):
            return "tp1"
    return "expired"


def compare_reviews(reviews: Sequence[JSON], m1: Sequence[Candle], now: datetime) -> JSON:
    """Approved candidates against those Opus held back, both scored the same way on M1 bars
    (TP1 before SL), so the page shows whether the AI's filter adds anything."""
    opens = [bar.open_time for bar in m1]
    counts: dict[str, Counter[str]] = {"approved": Counter(), "held": Counter()}
    for review in reviews:
        group = "approved" if review["decision"] == review["side"] else "held"
        start = datetime.fromisoformat(review["at"])
        until = datetime.fromisoformat(review["valid_until"])
        if until > now:
            counts[group]["open"] += 1
            continue
        window = m1[bisect.bisect_left(opens, start) : bisect.bisect_left(opens, until)]
        counts[group][first_touch(review["side"], review["stop_loss"], review["tp1"], window)] += 1
    out: JSON = {}
    for group, count in counts.items():
        decided = count["tp1"] + count["sl"]
        interval = wilson(count["tp1"], decided)
        out[group] = {
            "n": sum(count.values()),
            "tp1": count["tp1"],
            "sl": count["sl"],
            "expired": count["expired"],
            "open": count["open"],
            "tp1_rate": round(count["tp1"] / decided, 4) if decided else None,
            "ci95": [round(interval[0], 4), round(interval[1], 4)] if interval else None,
        }
    return out


# ------------------------------------------------------------------ the brief


def _num(value: float | None, pattern: str = "{:.2f}") -> str:
    return "-" if value is None else pattern.format(value)


def _accuracy(option: TfOption) -> str:
    tally = option.accuracy or {}
    calls, ci = int(tally.get("calls") or 0), tally.get("ci95")
    if not calls or not ci:
        return "belum ada data"
    rate = float(tally.get("rate") or 0)
    return f"{rate:.0%} (95%: {ci[0]:.0%}–{ci[1]:.0%}, {calls} candle)"


def _timeframe_row(option: TfOption, side: Side, context: MarketContext, settings: Settings) -> str:
    tf = option.tf
    if not option.eligible:
        return f"{tf} | tidak layak: {option.note}"
    state = context.timeframes[tf]
    engine = settings.signal_engine
    gap = None
    if context.mid is not None and state.ema20 is not None and state.atr14:
        gap = (context.mid - state.ema20) / state.atr14
    levels = compute_levels(side, context, engine.levels, tf)
    timing = entry_timing(side, context, tf, engine.max_chase_atr) or "siap"
    plan = "-"
    if levels is not None:
        plan = (
            f"entry {levels.entry_low:.2f}–{levels.entry_high:.2f}, SL {levels.stop_loss:.2f}, "
            f"TP1 {levels.tp1:.2f}, TP2 {levels.tp2:.2f}"
        )
    stoch = f"{_num(state.stoch_k, '{:.0f}')}/{_num(state.stoch_d, '{:.0f}')}"
    sar = "-" if state.sar_up is None else ("di bawah harga" if state.sar_up else "di atas harga")
    return " | ".join(
        [
            tf,
            "layak",
            f"ATR {_num(state.atr14)}",
            f"spread {option.cost_ratio or 0:.0%} dari SL",
            f"tren {TREND_ID.get(state.trend or '', '-')}",
            f"harga-EMA20 {_num(gap, '{:+.1f}')} ATR",
            f"RSI {_num(state.rsi14, '{:.0f}')}",
            f"Stoch {stoch}",
            f"SAR {sar}",
            f"timing: {timing}",
            plan,
            f"berlaku {engine.validity_bars * TF_MINUTES[tf]} mnt",
            f"akurasi sesi ini {_accuracy(option)}",
        ]
    )


def desk_brief(
    *,
    candidate: Signal,
    choice: TfChoice,
    breakdown: ScoreBreakdown,
    context: MarketContext,
    analyses: Sequence[Analysis],
    events: Sequence[CalendarEvent],
    active: Signal | None,
    settings: Settings,
    now: datetime,
) -> str:
    """Everything Opus needs, as tagged plain text (news text neutralised: it is data)."""
    c = breakdown.components
    session = SESSION_LABEL_ID.get(context.session, context.session)
    lines = [
        f"<waktu>{fmt_wib(now, with_date=True)} WIB · sesi {session} · "
        f"pasar {'buka' if context.market_open else 'tutup'}</waktu>",
        f"<harga>mid {_num(context.mid)} ({context.source or '-'}"
        f"{', proxy bukan XAUUSD spot' if context.proxy else ''}) · spread {_num(context.spread)}"
        "</harga>",
        f'<kandidat arah="{candidate.action}" skor="{candidate.composite_score:+.2f}" '
        f'ambang="{settings.signal_engine.entry_threshold:.2f}" '
        f'confidence="{candidate.confidence}">',
        f"komponen: berita {c.N:+.2f} · makro {c.M:+.2f} · teknikal {c.T:+.2f} · "
        f"harga {c.P:+.2f} (faktor sesi {c.session_factor:.2f})",
        "alasan mesin: " + "; ".join(candidate.top_reasons),
        "</kandidat>",
    ]
    if active is not None:
        lines.append(
            f"<sinyal_aktif>{active.action} {active.timeframe or ''} sejak "
            f"{fmt_wib(active.created_at_utc)} WIB, berlaku s/d {fmt_wib(active.valid_until_utc)} "
            "WIB</sinyal_aktif>"
        )
    else:
        lines.append("<sinyal_aktif>tidak ada</sinyal_aktif>")
    lines.append(
        f'<timeframe pilihan_sistem="{choice.tf}" dasar="{choice.basis}" '
        f'alasan="{untrusted(choice.reason, 200)}">'
    )
    side: Side = "BUY" if candidate.action == "BUY" else "SELL"
    for option in sorted(choice.options, key=lambda o: RANK.get(o.tf, 99)):
        lines.append(_timeframe_row(option, side, context, settings))
    lines.append("</timeframe>")
    lines.append("<berita>")
    recent = sorted(
        (a for a in analyses if a.triage.is_gold_relevant),
        key=lambda a: a.first_seen_at,
        reverse=True,
    )[:8]
    for analysis in recent:
        triage = analysis.triage
        status = "BELUM terkonfirmasi" if triage.needs_confirmation else "terkonfirmasi"
        text = untrusted(triage.summary_id or analysis.title, 300)
        lines.append(
            f"- [{fmt_wib(analysis.first_seen_at)} WIB, dampak {triage.impact_score}/5, "
            f"{triage.direction_gold}, {status}, priced-in {triage.priced_in_risk}] {text}"
        )
    if not recent:
        lines.append("- (tidak ada berita relevan yang masih berpengaruh)")
    lines.append("</berita>")
    upcoming = [
        e
        for e in sorted(events, key=lambda e: e.scheduled_at)
        if now <= e.scheduled_at <= now + timedelta(hours=4) and e.impact in ("high", "medium")
    ]
    lines.append("<event>")
    lines += [
        f"- {fmt_wib(e.scheduled_at)} WIB {e.country} {untrusted(e.title, 80)} [{e.impact}]"
        + (f" forecast {e.forecast}" if e.forecast else "")
        for e in upcoming
    ] or ["- tidak ada rilis penting dalam 4 jam"]
    lines.append("</event>")
    lines.append(
        f"<makro>USD z1h {_num(context.dxy_z_1h, '{:+.1f}')}, z15m "
        f"{_num(context.dxy_z_15m, '{:+.1f}')} ({context.dxy_source or '-'}); yield 10Y Δ1d "
        f"{_num(context.us10y_change_1d, '{:+.2f}')}; riil Δ1d "
        f"{_num(context.real_yield_change_1d, '{:+.2f}')}</makro>"
    )
    return "\n".join(lines)

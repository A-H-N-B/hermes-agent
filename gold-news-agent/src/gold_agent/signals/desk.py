"""The AI's final say on every signal: BUY / SELL / WAIT, and the chart to trade it on.

The engine proposes a candidate: a direction from news + macro + technical + price (or a
momentum impulse, signals/impulse.py), every hard filter passed, levels computed, its odds
estimated. `llm.desk_votes` independent Opus 5.5 answers (route `llm.desk`, effort low so a
review takes seconds) review the whole picture in parallel and a strict majority decides (a tie
holds): one unlucky answer cannot send or block a trade alone. An answer that does not come in
time counts for nothing. Guardrails keep that safe:

* it may approve the engine's direction or WAIT, never flip it: an opposite call is held as WAIT,
  since no evidence the system computed produced that trade;
* its timeframe must be one the selector found usable (data present, spread small next to SL);
* prices, SL and TP stay the engine's, recomputed on the chosen chart, never the model's.

A WAIT stands until something material changes (COOLDOWN, a score move, a new side or setup),
so a held candidate is not re-sent every evaluation. Every decision is logged (kv
`desk_reviews`) for the Performa page, which compares how approved and held-back candidates
would have done.
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
from gold_agent.market.physics_read import chart_rows, line, market_rows, meaning
from gold_agent.market.quant import REGIME_ID
from gold_agent.models import (
    Analysis,
    CalendarEvent,
    Candle,
    FundamentalSnapshot,
    MarketContext,
    Signal,
    SignalReview,
    TimeframeState,
)
from gold_agent.signals.engine import TF_MINUTES, entry_timing
from gold_agent.signals.forecast_score import wilson
from gold_agent.signals.levels import Side, compute_levels
from gold_agent.signals.scoring import ScoreBreakdown
from gold_agent.signals.timeframe import RANK, TfChoice, TfOption
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, fmt_wib

JSON = dict[str, Any]
MODEL_NAMES = {
    "claude-fable-5-1": "Fable 5.1",
    "claude-opus-5-5": "Opus 5.5",
    "claude-opus-5": "Opus 5",
    "claude-opus-4-8": "Opus 4.8",
    "claude-sonnet-5-5": "Sonnet 5.5",
}
KV_DESK = "desk_reviews"  # {"reviews": [...]}, newest last, for the Performa comparison
KEEP_REVIEWS = 300
COOLDOWN = timedelta(minutes=3)  # a WAIT stands this long ...
RESCORE = 0.10  # ... unless the composite score moves this much
APPROVAL_TTL = timedelta(minutes=2)  # an approval answers the candidate it was asked about
UNAVAILABLE_TTL = timedelta(minutes=5)  # then try the AI again
TREND_ID = {"up": "naik", "down": "turun", "flat": "datar"}

Reason = Annotated[str, Field(max_length=300)]


def model_name(model: str | None) -> str:
    return MODEL_NAMES.get(model or "", model or "AI")


def desk_label(settings: Settings) -> str:
    """Who decides, for people: "Opus 5.5"."""
    return model_name(settings.llm.desk.model)


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
    note: str | None = None  # why the AI did not decide (or not with every vote)
    model: str | None = None
    votes: int = 1  # answers counted
    agree: int = 1  # answers backing `decision`
    dissent: str | None = None  # an approval's strongest opposing reason
    models: tuple[str, ...] = ()  # every model that answered
    setup: str | None = None  # the candidate's setup type (a new kind of candidate asks again)

    @property
    def approved(self) -> bool:
        return self.decision == self.side

    @property
    def count(self) -> str:
        """The tally for a message, e.g. " (2/3 suara)"; empty for a single answer."""
        if self.votes <= 1:
            return ""
        return f" ({self.agree}/{self.votes} suara)"

    def review(self, note: str | None = None) -> SignalReview:
        decided = self.decision != "UNAVAILABLE"
        return SignalReview(
            model=self.model if decided else None,
            confidence=self.confidence,
            timeframe=self.timeframe,
            reasons=list(self.reasons),
            caution=self.caution,
            note=note or self.note,
            votes=self.votes if decided else None,
            agree=self.agree if decided else None,
            dissent=self.dissent,
            models=list(self.models),
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
            "votes": self.votes,
            "agree": self.agree,
            "dissent": self.dissent,
            "models": list(self.models),
        }


def verify(
    value: DeskDecision,
    *,
    candidate: Signal,
    choice: TfChoice,
    model: str,
    at: datetime,
) -> Verdict:
    """Apply the guardrails to what one committee member answered."""
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
        setup=candidate.setup_type,
    )


def tally(
    votes: Sequence[Verdict],
    failures: Sequence[str],
    *,
    candidate: Signal,
    choice: TfChoice,
    at: datetime,
) -> Verdict:
    """The answers to one candidate (each already through `verify`) as one verdict. The trade
    goes ahead only when more than half of the answers counted approve (a tie holds). The chart
    is the one most approving answers chose (a tie: the selector's pick if tied, else the most
    confident answer's). Reasons come from the most confident answer on the winning side;
    confidence is that side's mean, and an approval carries the strongest dissent. `failures`
    are answers that never came (timeout, budget): they count for nothing."""
    approving = [vote for vote in votes if vote.approved]
    holding = [vote for vote in votes if not vote.approved]
    approved = 2 * len(approving) > len(votes)
    side, other = (approving, holding) if approved else (holding, approving)
    lead = max(side, key=lambda vote: vote.confidence or 0)
    chosen = Counter(vote.timeframe for vote in side if vote.timeframe)
    timeframe = lead.timeframe
    if chosen:
        top = max(chosen.values())
        tied = [tf for tf, n in chosen.items() if n == top]
        if choice.tf in tied:
            timeframe = choice.tf
        elif timeframe not in tied:
            timeframe = tied[0]
    dissent = None
    if approved and other:
        strongest = max(other, key=lambda vote: vote.confidence or 0)
        dissent = strongest.reasons[0] if strongest.reasons else None
    sure = [vote.confidence for vote in side if vote.confidence is not None]
    asked = len(votes) + len(failures)
    return Verdict(
        side=candidate.action,
        decision=candidate.action if approved else "WAIT",
        at=at,
        composite=candidate.composite_score,
        timeframe=timeframe,
        confidence=round(sum(sure) / len(sure)) if sure else None,
        reasons=lead.reasons,
        caution=lead.caution,
        note=(
            f"{len(failures)} dari {asked} suara AI tidak jalan: {failures[0]}"
            if failures
            else None
        ),
        model=lead.model,
        votes=len(votes),
        agree=len(side),
        dissent=dissent,
        models=tuple(dict.fromkeys(vote.model for vote in votes if vote.model)),
        setup=candidate.setup_type,
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
        if verdict.setup is not None and verdict.setup != candidate.setup_type:
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
    """Approved candidates against those the AI held back, both scored the same way on M1 bars
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
            _statistics(state),
            f"timing: {timing}",
            plan,
            f"berlaku {engine.validity_bars * TF_MINUTES[tf]} mnt",
            f"akurasi sesi ini {_accuracy(option)}",
        ]
    )


def _statistics(state: TimeframeState) -> str:
    """The quant reads of one chart (market/quant.py) in one short field."""
    macd = "-" if state.macd_hist is None else ("naik" if state.macd_hist > 0 else "turun")
    vol = "-"
    if state.sigma is not None:
        pct = f", persentil {state.vol_pct:.0%}" if state.vol_pct is not None else ""
        vol = f"{state.sigma:.2f}$/bar ({state.sigma_model}{pct})"
    physics = line(chart_rows(state))
    return (
        f"rezim {REGIME_ID.get(state.regime or '', '-')} (ER {_num(state.efficiency)}, "
        f"Hurst {_num(state.hurst)}, DFA h {_num(state.dfa_h)}, ADX {_num(state.adx, '{:.0f}')}) · "
        f"MACD {macd} · tren Kalman t {_num(state.kalman_t, '{:+.1f}')} · volatilitas {vol}"
        + (f" · ekonofisika: {physics}" if physics else "")
    )


def _physics_lines(context: MarketContext) -> list[str]:
    """Market-wide econophysics (market/physics.py), computed by the program."""
    rows = market_rows(context.physics)
    if not rows:
        return ["<ekonofisika>data belum cukup (butuh beberapa hari data menit)</ekonofisika>"]
    return [
        "<ekonofisika>",
        *(f"- {r['label']}: {r['value']} → {meaning(r)}" for r in rows),
        "</ekonofisika>",
    ]


def _cross_lines(context: MarketContext) -> list[str]:
    lines = ["<lintas_aset>"]
    for asset in context.cross.values():
        moves = ", ".join(
            f"{span} {_num(value, '{:+.2f}')}%"
            for span, value in (
                ("15m", asset.change_15m_pct),
                ("1j", asset.change_1h_pct),
                ("1h", asset.change_1d_pct),
            )
        )
        lines.append(
            f"- {asset.label} [{untrusted(asset.symbol, 40)}]: {moves} · "
            f"z 1j {_num(asset.z_1h, '{:+.1f}')} · korelasi dengan emas {asset.corr:+.2f} "
            f"(bobot kini {asset.relevance:.0%}) · tarikan ke emas {_num(asset.effect, '{:+.2f}')}"
        )
    if len(lines) == 1:
        lines.append("- data pasar terkait (MT5) tidak tersedia")
    lines.append("</lintas_aset>")
    return lines


def _fundamental_lines(snapshot: FundamentalSnapshot | None) -> list[str]:
    if snapshot is None:
        return ["<fundamental>data fundamental harian tidak tersedia</fundamental>"]
    lines = [f"<fundamental>{snapshot.summary}"]
    for factor in snapshot.factors:
        lines.append(
            f"- {factor.label}: {factor.value}"
            + (f" ({factor.change})" if factor.change else "")
            + f" → {factor.note} [tarikan {factor.effect:+.2f}, data {factor.as_of or '-'}]"
        )
    if snapshot.missing:
        lines.append("- tanpa data: " + ", ".join(snapshot.missing))
    lines.append("</fundamental>")
    return lines


def _impulse_lines(context: MarketContext, candidate: Signal) -> list[str]:
    impulse = context.impulse
    if impulse is None:
        return ["<impuls>tidak ada gerakan luar biasa saat ini</impuls>"]
    extension = (
        f"{impulse.extension_atr:.1f} ATR"
        if impulse.extension_atr is not None
        else "tidak diketahui"
    )
    aligned = {True: "searah", False: "berlawanan", None: "belum diketahui"}[impulse.aligned]
    record = (candidate.impulse_note or "").partition("; ")[2] or "catatan impuls tidak dipakai"
    return [
        f'<impuls arah="{impulse.side}" z="{impulse.z:.1f}" gerak="{impulse.move:+.2f}$ dalam '
        f'{impulse.window} mnt" tembus="{impulse.level:.2f} (range {impulse.range_minutes} mnt)" '
        f'titik_awal="{impulse.trigger:.2f}" sudah_lewat="{extension}" tren_M15="{aligned}" '
        f'sejak="{fmt_wib(impulse.since)} WIB">catatan: {record}</impuls>'
    ]


def _odds_lines(candidate: Signal) -> list[str]:
    odds = candidate.odds
    if odds is None:
        return ["<peluang>tidak bisa dihitung (volatilitas belum tersedia)</peluang>"]
    band = f" (95%: {odds.ev_r_ci95[0]:+.2f}…{odds.ev_r_ci95[1]:+.2f}R)" if odds.ev_r_ci95 else ""
    learned = f"; {odds.meta_note}" if odds.meta_note else ""
    return [
        f"<peluang>{odds.note}; EV{band}; TP2 setelah TP1 {odds.tp2_after_tp1:.0%}; "
        f"model saja {odds.model_tp1:.0%}; {odds.drift_basis}; {odds.model_note or ''} selama "
        f"{odds.horizon_bars} candle; biaya spread {odds.cost_r:.2f}R{learned}</peluang>"
    ]


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
    fundamentals: FundamentalSnapshot | None = None,
) -> str:
    """Everything the committee needs, as tagged plain text (news text neutralised: it is
    data)."""
    c = breakdown.components
    session = SESSION_LABEL_ID.get(context.session, context.session)
    lines = [
        f"<waktu>{fmt_wib(now, with_date=True)} WIB · sesi {session} · "
        f"pasar {'buka' if context.market_open else 'tutup'}</waktu>",
        f"<harga>mid {_num(context.mid)} ({context.source or '-'}"
        f"{', proxy bukan XAUUSD spot' if context.proxy else ''}) · spread {_num(context.spread)}"
        "</harga>",
        f'<kandidat arah="{candidate.action}" setup="{candidate.setup_type}" '
        f'skor="{candidate.composite_score:+.2f}" '
        f'ambang="{settings.signal_engine.entry_threshold:.2f}" '
        f'confidence="{candidate.confidence}">',
        f"komponen: berita {c.N:+.2f} · makro {c.M:+.2f} · teknikal {c.T:+.2f} · "
        f"harga {c.P:+.2f} (faktor sesi {c.session_factor:.2f})",
        "alasan mesin: " + "; ".join(candidate.top_reasons),
        "</kandidat>",
        *_impulse_lines(context, candidate),
        *_odds_lines(candidate),
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
            f"{triage.direction_gold} (yakin {triage.confidence:.0%}), {status}, "
            f"priced-in {triage.priced_in_risk}] {text}"
        )
        deep = analysis.deep
        if deep is not None:  # the careful (effort high) reading of a high-impact story
            risks = "; ".join(untrusted(risk, 120) for risk in deep.risks[:2]) or "-"
            lines.append(
                f"  analisis mendalam: {deep.direction_gold} (yakin {deep.confidence:.0%}) · "
                f"{untrusted(deep.narrative_id, 400)} · risiko: {risks}"
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
    lines += _cross_lines(context)
    lines += _physics_lines(context)
    lines += _fundamental_lines(fundamentals)
    return "\n".join(lines)

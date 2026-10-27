"""24-hour outlook per WIB hour (11.3) and the Monday-open outlook (11.6).

The table is rule-based and reproducible from its inputs. The optional LLM narrative only
summarises the finished table; it never changes a number.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from gold_agent.analysis.prompts import untrusted
from gold_agent.config import Settings
from gold_agent.econ_calendar.events import EventCatalog
from gold_agent.market.levels import nearest_above, nearest_below
from gold_agent.models import (
    Analysis,
    CalendarEvent,
    MarketContext,
    Outlook,
    OutlookRow,
    Playbook,
    PlaybookBucket,
    Signal,
    SignalState,
)
from gold_agent.signals.profile import HourlyProfile
from gold_agent.timeutils.risky import risky_flags
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, SessionClock, fmt_wib

HOUR = timedelta(hours=1)
ROWS = 24
CLOSED = "PASAR TUTUP"
WAIT_DATA = "TUNGGU DATA"


def floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _value(number: float, unit: str) -> str:
    return f"{number:g}{unit}"


def _closest_bound(bucket: PlaybookBucket, forecast: float) -> tuple[str, float] | None:
    bounds = [b for b in (bucket.actual_min, bucket.actual_max) if b is not None]
    if not bounds:
        return None
    bound = min(bounds, key=lambda b: abs(b - forecast))
    return ("≥" if bound > forecast else "≤"), bound


def event_scenario(
    event: CalendarEvent, playbook: Playbook | None, catalog: EventCatalog | None = None
) -> str:
    """'US CPI 19:30: aktual ≥ 0.35% → SELL setelah konfirmasi; ≤ 0.25% → BUY ...'."""
    head = f"{event.title} {fmt_wib(event.scheduled_at)}"
    if playbook is not None and playbook.forecast is not None:
        buckets = {b.name: b for b in playbook.buckets}
        sell = _closest_bound(buckets["mild_bearish"], playbook.forecast)
        buy = _closest_bound(buckets["mild_bullish"], playbook.forecast)
        if sell and buy:
            unit = playbook.unit
            return (
                f"{head}: aktual {sell[0]} {_value(sell[1], unit)} → SELL setelah konfirmasi; "
                f"{buy[0]} {_value(buy[1], unit)} → BUY setelah konfirmasi; di antaranya netral"
            )
    spec = catalog.by_key(event.event_key) if catalog and event.event_key else None
    if spec is not None and event.forecast:
        above, below = ("SELL", "BUY") if spec.higher_is == "hawkish" else ("BUY", "SELL")
        return (
            f"{head}: di atas forecast {event.forecast} → condong {above}; "
            f"di bawah → condong {below} (keduanya setelah konfirmasi harga)"
        )
    return f"{head}: tunggu angka keluar, ikuti arah setelah konfirmasi harga"


@dataclass(frozen=True)
class OutlookInputs:
    now: datetime
    trigger: str
    context: MarketContext | None
    state: SignalState | None
    active: Signal | None
    events: Sequence[CalendarEvent]
    playbooks: Mapping[str, Playbook] = field(default_factory=lambda: dict[str, Playbook]())
    profile: HourlyProfile = field(default_factory=HourlyProfile)
    # event_key -> typical absolute 60-minute move in $ (event study, Phase 6)
    event_moves: Mapping[str, float] = field(default_factory=lambda: dict[str, float]())
    start: datetime | None = None  # first row; the current hour by default
    notes: Sequence[str] = ()


class OutlookBuilder:
    def __init__(
        self, settings: Settings, sessions: SessionClock, catalog: EventCatalog | None = None
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._catalog = catalog

    def build(self, inputs: OutlookInputs) -> Outlook:
        start = floor_hour(inputs.start or inputs.now)
        rows = [self._row(inputs, start + i * HOUR) for i in range(ROWS)]
        notes = list(inputs.notes)
        context = inputs.context
        if context is None or context.mid is None:
            notes.append("data harga tidak tersedia: bias dan level acuan tidak dihitung")
        elif context.proxy:
            notes.append(f"harga dari proxy ({context.source}), bukan XAUUSD broker")
        if inputs.profile.empty:
            h1 = context.timeframes.get("H1") if context else None
            notes.append(
                "profil statistik per jam belum cukup data: volatilitas memakai ATR H1"
                if h1 and h1.atr14
                else "profil statistik per jam dan ATR H1 belum ada: volatilitas tidak dihitung"
            )
        digest = hashlib.sha256(f"{inputs.trigger}|{inputs.now.isoformat()}".encode()).hexdigest()
        return Outlook(
            id=f"out-{digest[:16]}",
            created_at=inputs.now,
            trigger=inputs.trigger,
            rows=rows,
            notes=notes,
        )

    # ------------------------------------------------------------------ one row

    def _session_label(self, hour_start: datetime) -> tuple[str, str | None, bool]:
        """Sessions crossed in this hour (e.g. 'London → London–New York'), the main session
        id, and whether the market is open at all during the hour."""
        labels: list[str] = []
        main: str | None = None
        open_any = False
        for offset in (0, 30, 59):
            status = self._sessions.status(hour_start + timedelta(minutes=offset))
            label = SESSION_LABEL_ID[status.session]
            if status.open:
                open_any = True
                main = main or status.session
            elif status.reason:
                label = f"{label}: {status.reason}"
            if label not in labels:
                labels.append(label)
        return " → ".join(labels), main, open_any

    def _row(self, inputs: OutlookInputs, hour_start: datetime) -> OutlookRow:
        hour_end = hour_start + HOUR
        ahead = (hour_start - floor_hour(inputs.now)) / HOUR
        session_label, session, open_any = self._session_label(hour_start)
        in_hour = sorted(
            (
                e
                for e in inputs.events
                if hour_start <= e.scheduled_at < hour_end
                and e.impact in ("high", "medium")
                and not e.all_day
            ),
            key=lambda e: (e.impact != "high", e.scheduled_at),
        )
        high = [e for e in in_hour if e.impact == "high"]
        risky = risky_flags(
            hour_start, hour_end - timedelta(seconds=1), clock=self._sessions, events=inputs.events
        )
        label = f"{fmt_wib(hour_start)}–{fmt_wib(hour_end)}"
        event_texts = [f"{e.country} {e.title} {fmt_wib(e.scheduled_at)}" for e in in_hour]
        conditional = ahead >= self._settings.outlook.conditional_beyond_hours
        if not open_any or session is None:
            return OutlookRow(
                hour_start_utc=hour_start,
                label_wib=label,
                session=session_label,
                events=event_texts,
                expected_range_usd=None,
                volatility_label="Pasar tutup",
                bias=CLOSED,
                scenario="tidak ada sinyal saat pasar tutup",
                confidence=None,
                risky=risky,
                conditional=conditional,
            )
        expected, vol_label = self._volatility(inputs, hour_start, session, high)
        if high:
            bias = WAIT_DATA
            scenario = "; ".join(
                event_scenario(e, inputs.playbooks.get(e.id), self._catalog) for e in high
            )
            confidence = None
        elif not conditional:
            bias, scenario, confidence = self._near(inputs, hour_start, ahead)
        else:
            bias, scenario, confidence = self._conditional(inputs, hour_start, session, ahead)
        return OutlookRow(
            hour_start_utc=hour_start,
            label_wib=label,
            session=session_label,
            events=event_texts,
            expected_range_usd=expected,
            volatility_label=vol_label,
            bias=bias,
            scenario=scenario,
            confidence=confidence,
            risky=risky,
            conditional=conditional,
        )

    # ------------------------------------------------------------------ parts

    def _volatility(
        self,
        inputs: OutlookInputs,
        hour_start: datetime,
        session: str,
        high: Sequence[CalendarEvent],
    ) -> tuple[float | None, str]:
        profile = inputs.profile
        stats = profile.lookup(hour_start, session)
        typical = profile.typical_range()
        context = inputs.context
        h1 = context.timeframes.get("H1") if context else None
        atr_h1 = h1.atr14 if h1 else None
        if stats is not None and stats.median_range is not None:
            regime = _clip(atr_h1 / typical, 0.5, 3.0) if atr_h1 and typical else 1.0
            expected = stats.median_range * regime
            base = (typical or stats.median_range) * regime
        elif atr_h1:
            expected = base = atr_h1
        else:
            return None, "Tinggi (event)" if high else "data tidak tersedia"
        if high:
            multiplier = self._settings.outlook.event_range_multiplier
            keys = [e.event_key for e in high if e.event_key]
            measured = [inputs.event_moves[k] for k in keys if k in inputs.event_moves]
            expected = max([expected * multiplier, *measured])
            return round(expected, 1), "Tinggi"
        ratio = expected / base if base else 1.0
        label = "Rendah" if ratio < 0.75 else "Sedang" if ratio < 1.25 else "Tinggi"
        return round(expected, 1), label

    def _decayed(self, confidence: int | None, ahead: float) -> int | None:
        if confidence is None:
            return None
        decay = self._settings.outlook.near_confidence_decay_per_hour
        value = round(confidence * max(0.0, 1.0 - decay * ahead))
        return value or None

    def _lean(self, state: SignalState | None) -> str | None:
        if state is None:
            return None
        lean = 0.5 * self._settings.signal_engine.entry_threshold
        if state.composite_score >= lean:
            return "naik"
        if state.composite_score <= -lean:
            return "turun"
        return None

    def _near(
        self, inputs: OutlookInputs, hour_start: datetime, ahead: float
    ) -> tuple[str, str, int | None]:
        state, active = inputs.state, inputs.active
        confidence = self._decayed(state.confidence if state else None, ahead)
        if active is not None and active.action != "WAIT" and active.valid_until_utc > hour_start:
            return (
                active.action,
                f"sinyal {active.action} aktif s/d {active.valid_until_wib}: "
                f"SL {active.stop_loss:.2f}, TP1 {active.tp1:.2f}",
                confidence,
            )
        if state is None:
            return "Netral", "status sinyal belum tersedia", None
        lean = self._lean(state)
        bias = f"Condong {lean}" if lean else "Netral"
        threshold = self._settings.signal_engine.entry_threshold
        why = (
            state.wait_reasons[0]
            if state.wait_reasons
            else f"skor {state.composite_score:+.2f}, ambang ±{threshold:.2f}"
        )
        return bias, f"belum ada sinyal ({why})", confidence

    def _conditional(
        self, inputs: OutlookInputs, hour_start: datetime, session: str, ahead: float
    ) -> tuple[str, str, int | None]:
        state, context = inputs.state, inputs.context
        parts: list[str] = []
        if context is not None and context.mid is not None:
            above = nearest_above(context.levels, context.mid)
            below = nearest_below(context.levels, context.mid)
            if above is not None and below is not None:
                parts.append(
                    f"tembus {above.price:.2f} ({above.name}) → peluang lanjut naik; "
                    f"tembus {below.price:.2f} ({below.name}) → peluang turun"
                )
        stats = inputs.profile.lookup(hour_start, session)
        if (
            stats is not None
            and stats.pct_up is not None
            and stats.samples >= 2 * self._settings.outlook.profile_min_samples
            and abs(stats.pct_up - 0.5) >= 0.08
        ):
            parts.append(f"historis {stats.pct_up:.0%} candle jam ini naik ({stats.samples} hari)")
        if not parts:
            parts.append("tunggu sinyal terbentuk; belum ada level acuan")
        lean = self._lean(state)
        bias = f"Bersyarat (condong {lean})" if lean else "Bersyarat"
        confidence = self._decayed(state.confidence if state else None, ahead)
        if confidence is not None:
            confidence = min(confidence, self._settings.outlook.conditional_confidence_cap)
        return bias, "; ".join(parts), confidence


# --------------------------------------------------------------------------- weekend (11.6)


def weekend_notes(
    *,
    now: datetime,
    sessions: SessionClock,
    context: MarketContext | None,
    analyses: Sequence[Analysis],
    events: Sequence[CalendarEvent],
) -> list[str]:
    """Monday-open notes: proxy weekend drift as the gap estimate, weekend news, the week ahead."""
    open_at = sessions.next_weekly_open(now)
    notes = [f"Pasar buka {fmt_wib(open_at, with_date=True)} WIB"]
    drift = context.weekend_drift if context else None
    reference = context.weekend_reference if context else None
    if context is not None and drift is not None and reference:
        d1 = context.timeframes.get("D1")
        atr = d1.atr14 if d1 else None
        risk = "tidak diketahui (ATR D1 belum ada)"
        if atr:
            ratio = abs(drift) / atr
            risk = "tinggi" if ratio >= 0.5 else "sedang" if ratio >= 0.25 else "rendah"
        notes.append(
            f"{context.source or 'proxy'} bergerak {drift:+.2f} ({drift / reference:+.2%}) sejak "
            f"penutupan Jumat → estimasi gap {'naik' if drift > 0 else 'turun'}; risiko gap {risk}"
        )
    else:
        notes.append("estimasi gap tidak tersedia (tidak ada harga proxy akhir pekan)")
    since = sessions.last_weekly_close(now)
    weekend_news = sorted(
        (
            a
            for a in analyses
            if a.first_seen_at >= since and a.triage.is_gold_relevant and a.triage.impact_score >= 3
        ),
        key=lambda a: (-a.triage.impact_score, a.first_seen_at),
    )
    for analysis in weekend_news[:5]:
        triage = analysis.triage
        notes.append(
            f"Berita akhir pekan: {triage.summary_id} (dampak {triage.impact_score}/5, "
            f"{triage.direction_gold})"
        )
    if not weekend_news:
        notes.append("tidak ada berita akhir pekan berdampak ≥ 3")
    week = [
        e
        for e in events
        if open_at <= e.scheduled_at <= open_at + timedelta(days=5) and e.impact == "high"
    ]
    if week:
        agenda = "; ".join(
            f"{fmt_wib(e.scheduled_at, with_date=True)} {e.title}"
            for e in sorted(week, key=lambda e: e.scheduled_at)[:8]
        )
        notes.append(f"Agenda high-impact pekan ini: {agenda}")
    return notes


# --------------------------------------------------------------------------- LLM narrative


class OutlookNarrative(BaseModel):
    narrative_id: str = Field(max_length=1500)


def narrative_prompt(
    outlook: Outlook, state: SignalState | None, analyses: Sequence[Analysis]
) -> str:
    lines = ["<outlook>"]
    for row in outlook.rows:
        lines.append(
            " | ".join(
                [
                    row.label_wib,
                    row.session,
                    ", ".join(row.events) or "-",
                    f"{row.volatility_label} ({row.expected_range_usd or '-'})",
                    row.bias,
                    row.scenario,
                    str(row.confidence if row.confidence is not None else "-"),
                    "; ".join(row.risky) or "-",
                ]
            )
        )
    lines.append("</outlook>")
    if outlook.notes:
        lines.append("<notes>" + " | ".join(outlook.notes) + "</notes>")
    if state is not None:
        lines.append(
            f"<signal_state action={state.action} score={state.composite_score:+.2f} "
            f"confidence={state.confidence}>" + "; ".join(state.wait_reasons) + "</signal_state>"
        )
    lines.append("<news>")
    for analysis in analyses[:8]:
        triage = analysis.triage
        text = untrusted(triage.summary_id or analysis.title, 300)
        lines.append(f"- dampak {triage.impact_score}/5 {triage.direction_gold}: {text}")
    lines.append("</news>")
    return "\n".join(lines)

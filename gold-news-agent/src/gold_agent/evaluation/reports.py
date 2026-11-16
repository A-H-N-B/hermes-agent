"""Daily and weekly reports (section 17, C3.2), Bahasa Indonesia, numbers first.

Every rate carries its interval and small samples say so. An optional LLM narrative (brief route)
summarises the finished numbers; it never replaces or edits them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from gold_agent.analysis.prompts import untrusted
from gold_agent.core.app import AppContext
from gold_agent.evaluation.performance import Performance, summarize
from gold_agent.models import Alert, Analysis, Signal, SignalOutcome
from gold_agent.notify.format import esc
from gold_agent.ops.metrics import LatencySummary
from gold_agent.ops.metrics import summarize as latency_summary
from gold_agent.timeutils.sessions import WIB, fmt_wib, fmt_wib_date

ReportKind = Literal["harian", "mingguan"]


class ReportNarrative(BaseModel):
    narrative_id: str = Field(max_length=2000)


@dataclass
class ReportData:
    kind: ReportKind
    start: datetime
    end: datetime
    signals: list[Signal]
    outcomes: list[SignalOutcome]
    performance: Performance
    running: Performance  # last 90 days, for context
    news: list[Analysis]
    llm_cost: float
    latency: list[LatencySummary]
    alerts: list[Alert]
    narrative: str | None = None
    notes: list[str] = field(default_factory=lambda: list[str]())


def report_period(kind: ReportKind, now: datetime) -> tuple[datetime, datetime]:
    """Previous WIB day, or the previous Monday-Friday trading week (WIB dates)."""
    today = datetime.combine(now.astimezone(WIB).date(), time(0, 0), tzinfo=WIB)
    if kind == "harian":
        return today - timedelta(days=1), today
    monday = today - timedelta(days=today.weekday())
    if today.weekday() < 5:  # mid-week: report the previous full week
        monday -= timedelta(days=7)
    return monday, monday + timedelta(days=5)


async def collect(ctx: AppContext, kind: ReportKind, start: datetime, end: datetime) -> ReportData:
    signals = await ctx.signals.signals_between(start, end)
    ids = {s.signal_id for s in signals}
    outcomes = [o for o in await ctx.signals.outcomes_between(start, end) if o.signal_id in ids]
    since = end - timedelta(days=90)
    running = summarize(
        await ctx.signals.signals_between(since, end),
        await ctx.signals.outcomes_between(since, end),
    )
    news = sorted(
        (a for a in await ctx.news.analyses_between(start, end) if a.triage.impact_score >= 3),
        key=lambda a: (-a.triage.impact_score, a.first_seen_at),
    )
    alerts = [
        a
        for a in await ctx.ops.recent_alerts(limit=200)
        if start <= a.created_at < end and a.kind in ("health", "stale_price", "budget", "clock")
    ]
    llm_cost = await ctx.ops.llm_spend_since(start) - await ctx.ops.llm_spend_since(end)
    return ReportData(
        kind=kind,
        start=start,
        end=end,
        signals=signals,
        outcomes=outcomes,
        performance=summarize(signals, outcomes),
        running=running,
        news=news[:8],
        llm_cost=round(llm_cost, 4),
        latency=[
            s
            for s in latency_summary(await ctx.ops.latency_samples(start))
            if s.stage in ("triaged", "flash", "signal", "notified")
        ],
        alerts=alerts,
    )


def _rate(perf: Performance) -> str:
    if perf.win_rate is None:
        return "-"
    return f"{perf.win_rate:.0%} (95% CI {perf.wilson[0]:.0%}–{perf.wilson[1]:.0%})"


def _expectancy(perf: Performance) -> str:
    if perf.avg_r is None:
        return "-"
    ci = perf.expectancy_ci
    band = f" [{ci[0]:+.2f}…{ci[1]:+.2f}]" if ci else ""
    return f"{perf.avg_r:+.2f}R{band}"


def _title(data: ReportData) -> str:
    if data.kind == "harian":
        return f"Laporan harian · {fmt_wib_date(data.start)}"
    last_day = data.end - timedelta(seconds=1)
    return f"Laporan mingguan · {fmt_wib_date(data.start)} – {fmt_wib_date(last_day)}"


def render_markdown(data: ReportData) -> str:
    perf, running = data.performance, data.running
    lines = [f"# {_title(data)}", ""]
    if data.narrative:
        lines += [data.narrative, ""]
    lines += [
        "## Sinyal & paper trading",
        "",
        f"- Sinyal: {len(data.signals)} (BUY {sum(s.action == 'BUY' for s in data.signals)}, "
        f"SELL {sum(s.action == 'SELL' for s in data.signals)})",
        f"- Tertutup: {perf.n_closed} · win rate {_rate(perf)} · expectancy {_expectancy(perf)}"
        f" · total {perf.total_r:+.2f}R · max drawdown {perf.max_drawdown_r:.2f}R",
        f"- 90 hari terakhir: {running.n_closed} sinyal tertutup · win rate {_rate(running)} · "
        f"expectancy {_expectancy(running)}",
    ]
    lines += [f"- ⚠️ {note}" for note in running.notes]
    if data.outcomes:
        lines += ["", "| Sinyal | Hasil | R | MFE | MAE |", "|---|---|---|---|---|"]
        by_id = {s.signal_id: s for s in data.signals}
        for outcome in data.outcomes:
            signal = by_id.get(outcome.signal_id)
            label = f"{signal.action} {signal.created_at_wib}" if signal else outcome.signal_id
            r_value = "-" if outcome.r_multiple is None else f"{outcome.r_multiple:+.2f}"
            lines.append(
                f"| {label} | {outcome.status} | {r_value} | {outcome.mfe_r:+.2f} | "
                f"-{outcome.mae_r:.2f} |"
            )
    lines += ["", "## Berita penting", ""]
    if data.news:
        for analysis in data.news:
            triage = analysis.triage
            lines.append(
                f"- {fmt_wib(analysis.first_seen_at)} WIB · dampak {triage.impact_score}/5 · "
                f"{triage.direction_gold} · {triage.summary_id or analysis.title}"
            )
    else:
        lines.append("- Tidak ada berita berdampak ≥ 3.")
    lines += ["", "## Sistem", ""]
    lines.append(f"- Biaya LLM: ${data.llm_cost:.2f}")
    for stage in data.latency:
        lines.append(
            f"- Latensi {stage.stage} ({stage.source_id}): p50 {stage.p50_seconds:.1f} dtk, "
            f"p95 {stage.p95_seconds:.1f} dtk (n={stage.samples})"
        )
    if data.alerts:
        lines += [f"- Alert: {a.title}" for a in data.alerts[:10]]
    else:
        lines.append("- Tidak ada alert kesehatan.")
    lines += ["", "_Bukan saran keuangan. Hasil paper trading, bukan akun nyata._"]
    return "\n".join(lines)


def render_telegram(data: ReportData) -> str:
    perf, running = data.performance, data.running
    lines = [f"🧾 <b>{esc(_title(data))}</b>"]
    if data.narrative:
        lines.append(esc(data.narrative))
    lines += [
        f"Sinyal {len(data.signals)} · tertutup {perf.n_closed} · win rate {esc(_rate(perf))}",
        f"Expectancy {esc(_expectancy(perf))} · total {perf.total_r:+.2f}R",
        f"90 hari: {running.n_closed} tertutup · win rate {esc(_rate(running))} · expectancy "
        f"{esc(_expectancy(running))}",
    ]
    lines += [f"⚠️ {esc(note)}" for note in running.notes[:2]]
    for analysis in data.news[:3]:
        lines.append(f"📰 {esc(analysis.triage.summary_id or analysis.title)}")
    lines.append(f"Biaya LLM ${data.llm_cost:.2f} · alert kesehatan {len(data.alerts)}")
    lines.append("<i>Paper trading, bukan saran keuangan.</i>")
    return "\n".join(lines)


def narrative_prompt(data: ReportData) -> str:
    # News summaries inside the report are untrusted text: neutralise tag brackets.
    return f"<report kind={data.kind}>\n{untrusted(render_markdown(data), 12000)}\n</report>"

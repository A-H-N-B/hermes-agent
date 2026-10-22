"""Telegram message templates (section 15), Bahasa Indonesia, Telegram HTML.

Pure functions of their inputs: the caller passes `now`, so output is reproducible in tests.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from gold_agent.evaluation.performance import GroupStats, Performance
from gold_agent.models import (
    Alert,
    Analysis,
    CalendarEvent,
    MarketContext,
    Outlook,
    Playbook,
    PlaybookBucket,
    ReleaseReading,
    Signal,
    SignalOutcome,
    SignalState,
)
from gold_agent.notify.format import (
    DISCLAIMER,
    duration_id,
    esc,
    price,
    signed,
    wib_clock,
)
from gold_agent.signals.tvcode import tradingview_code
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, fmt_wib

BUCKET_ID = {
    "strong_bearish": "bearish kuat",
    "mild_bearish": "bearish ringan",
    "neutral": "netral",
    "mild_bullish": "bullish ringan",
    "strong_bullish": "bullish kuat",
    "unknown": "tidak diketahui",
}
ALERT_ICON = {
    "volatility": "⚡",
    "spread": "↔️",
    "health": "🩺",
    "budget": "💸",
    "clock": "🕰️",
    "stale_price": "⏱️",
    "recovery": "✅",
}
SEVERITY_ICON = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


# ------------------------------------------------------------------ signals


def render_signal(signal: Signal, *, now: datetime, next_event: CalendarEvent | None) -> str:
    icon = "🟢" if signal.action == "BUY" else "🔴"
    tp3 = f"TP3 {price(signal.tp3)}" + (
        f" ({esc(signal.tp3_source)})" if signal.tp3_source and signal.tp3_source != "3R" else ""
    )
    lines = [
        f"{icon} <b>{signal.action} XAUUSD</b> | Confidence {signal.confidence}/100",
        f"🕐 {wib_clock(signal.created_at_utc)} WIB · Sesi {esc(signal.session)}",
    ]
    if signal.timeframe:
        why = f" ({esc(signal.timeframe_reason)})" if signal.timeframe_reason else ""
        lines.append(f"📊 Chart {esc(signal.timeframe)}{why}")
    review = signal.review
    if review is not None and review.model:
        count = f" · {review.agree}/{review.votes} suara" if (review.votes or 0) > 1 else ""
        lines.append(
            f"🤖 Diputuskan Opus 5.5{count} · confidence AI {review.confidence or '-'}/100"
            + (f" · {esc(review.reasons[0])}" if review.reasons else "")
        )
        if review.caution:
            lines.append(f"⚠️ Catatan AI: {esc(review.caution)}")
        if review.dissent:
            lines.append(f"🗳️ Suara yang menahan: {esc(review.dissent)}")
    elif review is not None:
        lines.append(f"⚙️ Tanpa review AI: {esc(review.note or 'AI tidak tersedia')}")
    lines += [
        f"💵 Harga {price(signal.bid)}/{price(signal.ask)} · Spread {signal.spread:.2f}",
        f"🎯 Entry {price(signal.entry_low)}–{price(signal.entry_high)} ({signal.entry_type})",
        f"🛑 SL {price(signal.stop_loss)} ({signal.sl_distance:.2f}$ · "
        f"{signal.sl_atr_multiple:.2f}×ATR {esc(signal.timeframe or 'M15')})",
        f"✅ TP1 {price(signal.tp1)} (1R) · TP2 {price(signal.tp2)} (2R) · {tp3}",
        f"⏳ Berlaku s/d {esc(signal.valid_until_wib)}",
    ]
    if signal.trigger_summary:
        trigger = f"📰 Pemicu: {esc(signal.trigger_summary)} — {esc(signal.trigger_source or '-')}"
        if signal.first_seen_at is not None:
            seconds = (signal.created_at_utc - signal.first_seen_at).total_seconds()
            trigger += f" (diterima {seconds:.0f} dtk)"
        lines.append(trigger)
    else:
        lines.append("📰 Pemicu: makro/teknikal (tanpa berita terkonfirmasi)")
    c = signal.components
    lines.append(
        f"📊 Berita {signed(c.N)} · Makro {signed(c.M)} · Teknikal {signed(c.T)} · "
        f"Konfirmasi {signed(c.P)}"
    )
    lines.append("🧾 Alasan: " + "; ".join(esc(r) for r in signal.top_reasons))
    lines.append("⚠️ Batal jika: " + "; ".join(esc(r) for r in signal.invalidation[:4]))
    if next_event is not None:
        countdown = duration_id((next_event.scheduled_at - now).total_seconds())
        lines.append(
            f"📅 Event berikutnya: {esc(next_event.country)} {esc(next_event.title)} pukul "
            f"{fmt_wib(next_event.scheduled_at)} WIB ({countdown} lagi)"
        )
    if signal.lot_size is not None:
        lines.append(f"📦 Lot {signal.lot_size:g} (risiko {signal.risk_pct:g}% per posisi)")
    else:
        lines.append(f"📦 Lot: {esc(signal.lot_note or 'belum bisa dihitung')}")
    if signal.proxy_price:
        lines.append(
            f"ℹ️ Harga dari proxy ({esc(signal.price_source)}), bukan XAUUSD broker: "
            "sesuaikan level dengan harga brokermu"
        )
    lines.append(
        f"📋 Kode TradingView (ketuk untuk salin): <code>{tradingview_code(signal)}</code>"
    )
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def render_signal_replaced(old: Signal, new_id: str, new_action: str) -> str:
    return "\n".join(
        [
            f"♻️ <b>Sinyal {old.action} dibatalkan</b> (dibuat {esc(old.created_at_wib)})",
            f"Arah berbalik menjadi {esc(new_action)} — lihat sinyal {esc(new_id)}.",
            DISCLAIMER,
        ]
    )


CLOSE_REASON = {
    "tp2": "TP2 tercapai",
    "tp1": "TP1 tercapai, sisa ditutup karena waktu habis",
    "breakeven": "TP1 tercapai, sisa kena stop breakeven",
    "sl": "kena stop loss",
    "expired": "kedaluwarsa sebelum TP1",
    "cancelled": "dibatalkan",
}


def render_paper_tp1(signal: Signal, outcome: SignalOutcome) -> str:
    stop = price(outcome.stop) if outcome.stop is not None else "-"
    return "\n".join(
        [
            f"✅ <b>TP1 tercapai</b> · {signal.action} {esc(signal.created_at_wib)}",
            f"Paper: {outcome.realized_r:+.2f}R sudah diamankan · geser SL ke breakeven {stop}",
            f"Sisa posisi menuju TP2 {price(signal.tp2)}. Bukan saran keuangan.",
        ]
    )


def render_paper_close(signal: Signal, outcome: SignalOutcome) -> str:
    r_value = outcome.r_multiple if outcome.r_multiple is not None else 0.0
    icon = "📗" if r_value > 0 else "📕" if r_value < 0 else "📘"
    duration = duration_id((outcome.duration_minutes or 0.0) * 60)
    return "\n".join(
        [
            f"{icon} <b>Sinyal {signal.action} ditutup</b> · {esc(signal.created_at_wib)} · "
            f"{CLOSE_REASON.get(outcome.status, outcome.status)}",
            f"Hasil paper {r_value:+.2f}R · entry {price(outcome.entry_price)} → keluar "
            f"{price(outcome.exit_price)} · durasi {duration}",
            f"MFE {outcome.mfe_r:+.2f}R · MAE -{outcome.mae_r:.2f}R",
        ]
    )


# ------------------------------------------------------------------ fast path


def release_headline(release: ReleaseReading) -> str:
    unit = release.unit
    parts = [f"{esc(release.event_name)} {release.actual:g}{esc(unit)}"]
    extra: list[str] = []
    if release.forecast is not None:
        extra.append(f"forecast {release.forecast:g}{esc(unit)}")
    if release.previous is not None:
        extra.append(f"sebelumnya {release.previous:g}{esc(unit)}")
    if extra:
        parts.append(f"({', '.join(extra)})")
    return " ".join(parts)


def render_flash(
    release: ReleaseReading, *, now: datetime, confirm_seconds: int, news_trading: bool
) -> str:
    lines = [f"🔴 <b>FLASH</b> | {release_headline(release)}"]
    if release.surprise is not None:
        z = f" (z={release.z:+.1f})" if release.z is not None else ""
        lines.append(
            f"Surprise {release.surprise:+g}{esc(release.unit)}{z} → "
            f"{esc(release.direction_gold)} emas · skenario playbook: "
            f"{BUCKET_ID.get(release.bucket, release.bucket)}"
        )
    else:
        lines.append("Forecast tidak tersedia: arah belum bisa dinilai dari surprise")
    if not news_trading:
        lines.append(f"Menunggu konfirmasi harga {confirm_seconds} dtk sebelum sinyal…")
    latency = (now - release.first_seen_at).total_seconds()
    lines.append(
        f"Diterima {wib_clock(release.first_seen_at, seconds=True)} WIB · latensi {latency:.1f} dtk"
    )
    if release.z_basis == "prior":
        lines.append("<i>z-score memakai simpangan baku awal (belum terkalibrasi)</i>")
    return "\n".join(lines)


# ------------------------------------------------------------------ news + alerts


def render_news(analysis: Analysis, *, now: datetime) -> str:
    triage = analysis.triage
    icon = "🔴" if triage.impact_score >= 4 else "🟡"
    head = "BERITA PENTING" if triage.impact_score >= 4 else "BERITA"
    lines = [
        f"{icon} <b>{head}</b> · dampak {triage.impact_score}/5 · {esc(triage.direction_gold)} "
        f"· {esc(triage.category)}",
        esc(triage.summary_id or analysis.title),
    ]
    latency = (now - analysis.first_seen_at).total_seconds()
    meta = (
        f"Sumber: {esc(', '.join(analysis.source_ids[:3]))} · diterima "
        f"{wib_clock(analysis.first_seen_at)} WIB ({duration_id(latency)} lalu)"
    )
    if triage.needs_confirmation:
        meta += " · <b>belum terkonfirmasi</b>"
    if analysis.ai_pending:
        meta += " · analisis aturan (AI tertunda)"
    lines.append(meta)
    return "\n".join(lines)


def render_alert(alert: Alert) -> str:
    icon = ALERT_ICON.get(alert.kind, SEVERITY_ICON.get(alert.severity, "🔔"))
    return "\n".join(
        [
            f"{icon} <b>{esc(alert.title)}</b>",
            esc(alert.detail),
            f"{fmt_wib(alert.created_at, with_date=True)} WIB",
        ]
    )


# ------------------------------------------------------------------ outlook + playbook


def render_outlook(outlook: Outlook) -> str:
    lines = [
        f"🗓️ <b>OUTLOOK 24 JAM</b> · {fmt_wib(outlook.created_at, with_date=True)} WIB "
        f"({esc(outlook.trigger)})"
    ]
    scenario_shown = False
    for row in outlook.rows:
        hours = row.label_wib.replace(":00", "")
        if row.bias == "PASAR TUTUP":
            lines.append(f"{hours} · {esc(row.session)}")
            continue
        volatility = row.volatility_label
        if row.expected_range_usd is not None:
            volatility += f" ~${row.expected_range_usd:.0f}"
        confidence = f" ({row.confidence})" if row.confidence is not None else ""
        events = f" · 📌 {esc(', '.join(row.events))}" if row.events else ""
        lines.append(
            f"<b>{hours}</b> {esc(row.session)} · {esc(volatility)} · "
            f"{esc(row.bias)}{confidence}{events}"
        )
        if row.events or (row.conditional and not scenario_shown):
            lines.append(f"   ↳ {esc(row.scenario)}")
            scenario_shown = scenario_shown or row.conditional
    if outlook.narrative_id:
        lines.append(f"📝 {esc(outlook.narrative_id)}")
    for note in outlook.notes:
        lines.append(f"• {esc(note)}")
    lines.append("<i>Jam &gt; 4 jam ke depan bersifat skenario. Bukan saran keuangan.</i>")
    return "\n".join(lines)


def _bucket_range(bucket: PlaybookBucket, unit: str) -> str:
    low, high = bucket.actual_min, bucket.actual_max
    if low is None and high is None:
        z_low, z_high = bucket.hawkish_z_min, bucket.hawkish_z_max
        if z_low is None:
            return f"z hawkish ≤ {z_high:+.1f}"
        if z_high is None:
            return f"z hawkish ≥ {z_low:+.1f}"
        return f"z hawkish {z_low:+.1f}…{z_high:+.1f}"
    if low is None:
        return f"≤ {high:g}{unit}"
    if high is None:
        return f"≥ {low:g}{unit}"
    return f"{low:g}–{high:g}{unit}"


def _bucket_order(bucket: PlaybookBucket) -> float:
    """Highest actual first: open-ended top bucket, then by midpoint, open-ended bottom last."""
    low, high = bucket.actual_min, bucket.actual_max
    if low is not None and high is not None:
        return -(low + high) / 2
    if low is not None:
        return -math.inf
    if high is not None:
        return math.inf
    return 0.0


def render_playbook(playbook: Playbook, *, now: datetime, country: str = "USD") -> str:
    unit = esc(playbook.unit)
    minutes = duration_id((playbook.scheduled_at - now).total_seconds())
    lines = [
        f"📋 <b>PLAYBOOK</b> · {esc(country)} {esc(playbook.event_title)} · "
        f"{fmt_wib(playbook.scheduled_at)} WIB ({minutes} lagi)"
    ]
    facts: list[str] = []
    if playbook.forecast is not None:
        facts.append(f"Forecast {playbook.forecast:g}{unit}")
    if playbook.previous is not None:
        facts.append(f"sebelumnya {playbook.previous:g}{unit}")
    basis = "terkalibrasi" if playbook.z_basis == "calibrated" else "perkiraan awal"
    facts.append(f"σ surprise {playbook.surprise_std:g}{unit} ({basis})")
    lines.append(" · ".join(facts))
    has_actuals = any(
        b.actual_min is not None or b.actual_max is not None for b in playbook.buckets
    )
    buckets = sorted(playbook.buckets, key=_bucket_order) if has_actuals else playbook.buckets
    for bucket in buckets:
        lines.append(f"• {_bucket_range(bucket, unit)} → {esc(bucket.hint_id)}")
    for note in playbook.notes:
        lines.append(f"<i>{esc(note)}</i>")
    lines.append("Sinyal baru tetap menunggu konfirmasi harga setelah rilis. Bukan saran keuangan.")
    return "\n".join(lines)


# ------------------------------------------------------------------ bot replies


def render_status(
    *,
    now: datetime,
    state: SignalState | None,
    context: MarketContext | None,
    active: Signal | None,
    next_event: CalendarEvent | None,
    muted_until: datetime | None,
    mode: str,
) -> str:
    lines = [f"📍 <b>STATUS</b> · {fmt_wib(now, with_date=True)} WIB · mode {esc(mode)}"]
    if context is None or context.mid is None:
        lines.append("💵 Harga: data tidak tersedia")
    else:
        source = esc(context.source or "-") + (" (proxy)" if context.proxy else "")
        age = f" · tick {context.tick_age_seconds:.0f} dtk" if context.tick_age_seconds else ""
        stale = " · <b>BASI</b>" if context.stale else ""
        lines.append(
            f"💵 {price(context.mid)} ({source}) · spread {price(context.spread)}{age}{stale}"
        )
        lines.append(
            f"🕐 Sesi {esc(SESSION_LABEL_ID.get(context.session, context.session))} · pasar "
            + ("buka" if context.market_open else "tutup")
        )
    if state is None:
        lines.append("📊 Status sinyal belum tersedia")
    else:
        lines.append(
            f"📊 {state.action} · skor {signed(state.composite_score)} · "
            f"confidence {state.confidence}"
        )
        for reason in state.wait_reasons[:4]:
            lines.append(f"   ⏸ {esc(reason)}")
        if state.halted:
            lines.append("🛑 Kill switch aktif (/resume untuk melanjutkan)")
    if active is not None:
        lines.append(
            f"🎯 Sinyal aktif: {active.action} {price(active.entry_low)}–{price(active.entry_high)}"
            f" · SL {price(active.stop_loss)} · TP1 {price(active.tp1)} · s/d "
            f"{esc(active.valid_until_wib)}"
        )
    else:
        lines.append("🎯 Tidak ada sinyal aktif")
    if next_event is not None:
        countdown = duration_id((next_event.scheduled_at - now).total_seconds())
        lines.append(
            f"📅 {esc(next_event.country)} {esc(next_event.title)} "
            f"{fmt_wib(next_event.scheduled_at, with_date=True)} WIB ({countdown} lagi)"
        )
    if muted_until is not None and muted_until > now:
        lines.append(f"🔕 Notifikasi dibisukan s/d {fmt_wib(muted_until)} WIB (kecuali 🔴)")
    return "\n".join(lines)


def render_calendar(events: Sequence[CalendarEvent], *, now: datetime) -> str:
    if not events:
        return "📅 Tidak ada event high/medium impact dalam 48 jam ke depan."
    lines = ["📅 <b>KALENDER</b> (48 jam, WIB)"]
    for event in events:
        icon = "🔴" if event.impact == "high" else "🟡"
        numbers = [
            f"{label} {value}"
            for label, value in (
                ("F", event.forecast),
                ("P", event.previous),
                ("A", event.actual),
            )
            if value
        ]
        countdown = (
            f" · {duration_id((event.scheduled_at - now).total_seconds())} lagi"
            if event.scheduled_at > now
            else ""
        )
        when = "sepanjang hari" if event.all_day else fmt_wib(event.scheduled_at, with_date=True)
        lines.append(
            f"{icon} {when} {esc(event.country)} {esc(event.title)}"
            + (f" ({esc(', '.join(numbers))})" if numbers else "")
            + countdown
        )
    return "\n".join(lines)


def render_digest(
    items: Sequence[tuple[datetime, str, bool]], *, max_lines_per_item: int = 3
) -> str:
    """Merge held notifications into one message (quiet hours, mute, rate limit, digest).
    Items flagged `full` (🔴: signals, flashes, playbooks) keep every line."""
    first, last = items[0][0], items[-1][0]
    lines = [
        f"🔵 <b>RINGKASAN</b> · {len(items)} pembaruan ({wib_clock(first)}–{wib_clock(last)} WIB)"
    ]
    for created_at, text, full in items:
        body = text.split("\n")
        clipped = body if full else body[:max_lines_per_item]
        if len(body) > len(clipped):
            clipped.append("   … (detail di dashboard)")
        lines.append("")
        lines.append(f"<i>{wib_clock(created_at)}</i> " + clipped[0])
        lines.extend(clipped[1:])
    return "\n".join(lines)


def _rate(stats: GroupStats) -> str:
    if stats.win_rate is None:
        return "-"
    low, high = stats.wilson
    return f"{stats.win_rate:.0%} [{low:.0%}–{high:.0%}]"


def render_stats(perf: Performance) -> str:
    lines = [
        "📈 <b>PERFORMA PAPER TRADING</b>",
        f"Sinyal {perf.n_signals} · tertutup {perf.n_closed} · terbuka {perf.n_open}",
    ]
    if perf.n_closed:
        ci = (
            f" [{perf.expectancy_ci[0]:+.2f}…{perf.expectancy_ci[1]:+.2f}]"
            if perf.expectancy_ci
            else ""
        )
        win_rate = perf.win_rate or 0.0
        lines += [
            f"Win rate {win_rate:.0%} (95% CI {perf.wilson[0]:.0%}–{perf.wilson[1]:.0%})",
            f"Expectancy {perf.avg_r or 0.0:+.2f}R{ci} · total {perf.total_r:+.2f}R · "
            f"max drawdown {perf.max_drawdown_r:.2f}R",
        ]
        best = [g for g in perf.by_category if g.n >= 5]
        if best:
            lines.append(
                "Per kategori: " + "; ".join(f"{esc(g.label)} {g.n}× {_rate(g)}" for g in best[:6])
            )
    for note in perf.notes:
        lines.append(f"⚠️ {esc(note)}")
    return "\n".join(lines)

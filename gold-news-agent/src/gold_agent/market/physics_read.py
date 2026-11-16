"""The econophysics numbers in plain Bahasa Indonesia, shared by the dashboard cards, the AI
brief and Telegram, so each threshold lives in one place (market/econophysics.py) and every
reader sees the same words.

A reading is a row: label, value, a short tag (the dashboard's coloured word), an optional
detail line, and a tone for the colour ("ok" = helps a forecast, "warn" = a risk, "up" / "down"
= what it implies for gold, "" = neutral).
"""

from __future__ import annotations

from gold_agent.market import econophysics as ep
from gold_agent.models import MarketPhysics, TimeframeState

Row = dict[str, str]


def _row(label: str, value: str, tag: str, detail: str = "", tone: str = "") -> Row:
    return {"label": label, "value": value, "tag": tag, "detail": detail, "tone": tone}


def meaning(row: Row) -> str:
    return f"{row['tag']}: {row['detail']}" if row["detail"] else row["tag"]


def chart_rows(state: TimeframeState) -> list[Row]:
    """The physics of one chart (complete bars)."""
    rows: list[Row] = []
    if state.tail_alpha is not None:
        alpha = state.tail_alpha
        value = f"α {alpha:.1f}" + (f" · ν {state.tail_nu:g}" if state.tail_nu else "")
        if alpha < ep.ALPHA_VERY_FAT:
            rows.append(_row("Ekor", value, "sangat tebal", "lonjakan ekstrem sering", "warn"))
        elif alpha < ep.ALPHA_FAT:
            rows.append(_row("Ekor", value, "tebal", "lonjakan lebih sering dari normal"))
        else:
            rows.append(_row("Ekor", value, "normal"))
    if state.entropy is not None:
        value = f"entropi {state.entropy:.3f}"
        if state.entropy < ep.PE_STRUCTURED:
            rows.append(
                _row(
                    "Keteraturan", value, "terstruktur", "ada pola berulang yang bisa dibaca", "ok"
                )
            )
        else:
            rows.append(_row("Keteraturan", value, "hampir acak", "pola chart lemah", "warn"))
    if state.mf_width is not None:
        value = f"Δh {state.mf_width:.2f}"
        if state.mf_width >= ep.MF_STRONG:
            rows.append(
                _row("Multifraktal", value, "kuat", "gerak tidak seragam, rawan lonjakan", "warn")
            )
        elif state.mf_width >= ep.MF_MODERATE:
            rows.append(_row("Multifraktal", value, "sedang", "umum di pasar"))
        else:
            rows.append(_row("Multifraktal", value, "teratur"))
    if state.ou_half_life is not None and state.ou_mean is not None:
        rows.append(
            _row(
                "Mean reversion",
                f"half-life {state.ou_half_life:.0f} candle",
                "nyata",
                f"harga cenderung kembali ke {state.ou_mean:.2f}",
                "ok",
            )
        )
    elif state.ou_t is not None:
        rows.append(
            _row("Mean reversion", f"DF t {state.ou_t:+.1f}", "tidak nyata", "bergerak bebas")
        )
    return rows


def _aftershock_rows(physics: MarketPhysics) -> list[Row]:
    shock = physics.aftershock
    if shock is None:
        return []
    running = shock.intensity_ratio >= ep.AFTERSHOCK_HIGH
    likely = shock.next_hour >= 0.5
    fades = f", mereda ±{shock.half_life_minutes:.0f} mnt" if shock.clustered else ""
    return [
        _row(
            "Badai susulan (Hawkes)",
            f"{shock.intensity_ratio:.1f}× rata-rata",
            "berlangsung" if running else "tenang",
            "lonjakan berikutnya lebih mungkin" if running else "",
            "warn" if running else "",
        ),
        _row(
            "Gerak besar 1 jam ke depan",
            f"peluang {shock.next_hour:.0%}",
            "waspada" if likely else "biasa",
            f"gerak > {shock.threshold_pct:.2f}% per menit",
            "warn" if likely else "",
        ),
        _row(
            "Endogenitas pasar",
            f"{shock.branching:.0%}",
            "terbukti" if shock.clustered else "belum terbukti",
            f"gerak besar yang dipicu gerak sebelumnya{fades}",
        ),
    ]


def _leader_rows(physics: MarketPhysics) -> list[Row]:
    leading = [leader for leader in physics.leaders if leader.leads]
    if physics.leaders and not leading:
        return [
            _row(
                "Pemimpin emas",
                "–",
                "tidak ada",
                "belum ada pasar yang terbukti bergerak lebih dulu",
            )
        ]
    rows: list[Row] = []
    for leader in leading[:2]:
        move = f"{leader.last_move_pct:+.3f}%" if leader.last_move_pct is not None else "–"
        rows.append(
            _row(
                "Pemimpin emas",
                leader.label,
                {1: "emas cenderung naik", -1: "emas cenderung turun"}.get(
                    leader.implied, "tanpa arah"
                ),
                f"bergerak ±1 menit lebih dulu (z {leader.z:.1f}); gerak terakhir {move}",
                {1: "up", -1: "down"}.get(leader.implied, ""),
            )
        )
    return rows


def market_rows(physics: MarketPhysics | None) -> list[Row]:
    """Market-wide physics: aftershocks, leaders, the common factor and bubble risk."""
    if physics is None:
        return []
    rows = _aftershock_rows(physics) + _leader_rows(physics)
    common = physics.common
    if common is not None:
        rows.append(
            _row(
                "Faktor bersama (RMT)",
                f"{common.factors} faktor · {common.share:.0%} varian",
                f"emas ikut {common.gold_loading:+.2f}" if common.factors else "tidak ada",
                "gerak bersama yang nyata (di atas batas acak)"
                if common.factors
                else "gerak pasar tidak lebih bersama dari kebetulan",
            )
        )
    bubble = physics.bubble
    if bubble is not None:
        value = f"{bubble.bubble:.0%} jendela"
        if bubble.bubble >= ep.BUBBLE_ALERT:
            when = (
                f", titik kritis ±{bubble.days_to_tc:.0f} hari bursa" if bubble.days_to_tc else ""
            )
            detail = f"koreksi besar mungkin (harian, eksperimental{when})"
            rows.append(_row("Risiko gelembung (LPPLS)", value, "waspada", detail, "warn"))
        elif bubble.anti_bubble >= ep.BUBBLE_ALERT:
            detail = "pantulan naik mungkin dekat (eksperimental)"
            rows.append(_row("Risiko gelembung (LPPLS)", value, "anti-gelembung", detail))
        else:
            rows.append(_row("Risiko gelembung (LPPLS)", value, "tidak ada"))
    return rows


def line(rows: list[Row]) -> str:
    return "; ".join(f"{r['label']} {r['value']} ({meaning(r)})" for r in rows)


def signal_note(state: TimeframeState | None, physics: MarketPhysics | None) -> str | None:
    """One short line for a signal: the chart's order and tails, and any market-wide warning or
    lead."""
    parts: list[str] = []
    if state is not None:
        for row in chart_rows(state):
            if row["label"] in ("Keteraturan", "Ekor") or row["tone"] == "ok":
                parts.append(f"{row['label'].lower()} {row['tag']}")
    for row in market_rows(physics):
        if row["tone"]:
            parts.append(f"{row['label']} {row['value']}: {row['tag']}")
    return " · ".join(parts) if parts else None

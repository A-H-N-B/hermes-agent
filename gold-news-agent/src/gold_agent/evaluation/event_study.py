"""Event study (section 17, C3.4): how far gold moved 1/5/15/60 minutes after each story was first
seen, against the direction predicted. Uses only our own archive (first_seen_at), because third-
party news timestamps are not reliable; its statistical power grows as the archive grows."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from gold_agent.market.series import PriceSeries
from gold_agent.models import Analysis
from gold_agent.models.common import DIRECTION_SIGN
from gold_agent.stats import wilson_interval

HORIZONS = (1, 5, 15, 60)  # minutes
MIN_SAMPLES = 30  # below this a group is labelled "sampel belum cukup"


@dataclass(frozen=True)
class EventMove:
    cluster_id: str
    category: str
    source_id: str
    impact: int
    direction: str
    event_key: str | None
    first_seen_at: datetime
    base: float
    moves: dict[int, float | None] = field(default_factory=lambda: dict[int, float | None]())

    def signed(self, horizon: int) -> float | None:
        """Move in the predicted direction ($); None without a direction or a price."""
        sign = DIRECTION_SIGN.get(self.direction, 0)
        move = self.moves.get(horizon)
        return None if sign == 0 or move is None else sign * move


@dataclass(frozen=True)
class GroupStat:
    label: str
    n: int  # stories with a direction and a price at the horizon
    hits: int
    hit_rate: float | None
    wilson: tuple[float, float]
    mean_signed: float | None  # average $ in the predicted direction
    median_abs: float | None  # typical size of the move, whatever its direction

    @property
    def enough(self) -> bool:
        return self.n >= MIN_SAMPLES


def measure(analyses: Iterable[Analysis], prices: PriceSeries) -> list[EventMove]:
    moves: list[EventMove] = []
    for analysis in analyses:
        triage = analysis.triage
        if not triage.is_gold_relevant:
            continue
        base = prices.at(analysis.first_seen_at)
        if base is None:
            continue
        horizon_moves: dict[int, float | None] = {}
        for minutes in HORIZONS:
            later = prices.at(analysis.first_seen_at + timedelta(minutes=minutes))
            horizon_moves[minutes] = None if later is None else round(later - base, 3)
        moves.append(
            EventMove(
                cluster_id=analysis.cluster_id,
                category=triage.category,
                source_id=analysis.source_ids[0] if analysis.source_ids else "-",
                impact=triage.impact_score,
                direction=triage.direction_gold,
                event_key=analysis.release.event_key if analysis.release else None,
                first_seen_at=analysis.first_seen_at,
                base=base,
                moves=horizon_moves,
            )
        )
    return moves


def group(
    moves: Sequence[EventMove], key: Callable[[EventMove], str | None], horizon: int
) -> list[GroupStat]:
    buckets: dict[str, list[EventMove]] = defaultdict(list)
    for move in moves:
        label = key(move)
        if label is not None:
            buckets[label].append(move)
    stats: list[GroupStat] = []
    for label, members in sorted(buckets.items()):
        signed = [s for m in members if (s := m.signed(horizon)) is not None]
        sizes = [abs(v) for m in members if (v := m.moves.get(horizon)) is not None]
        hits = sum(1 for s in signed if s > 0)
        stats.append(
            GroupStat(
                label=label,
                n=len(signed),
                hits=hits,
                hit_rate=hits / len(signed) if signed else None,
                wilson=wilson_interval(hits, len(signed)),
                mean_signed=round(statistics.fmean(signed), 3) if signed else None,
                median_abs=round(statistics.median(sizes), 3) if sizes else None,
            )
        )
    return stats


def typical_event_moves(
    moves: Sequence[EventMove], *, min_samples: int, horizon: int = 60
) -> dict[str, float]:
    """event_key -> median |move| after the release (feeds the outlook's event-hour range)."""
    return {
        g.label: g.median_abs
        for g in group(moves, lambda m: m.event_key, horizon)
        if g.median_abs is not None
        and sum(1 for m in moves if m.event_key == g.label) >= min_samples
    }


def _row(stat: GroupStat) -> str:
    rate = "-" if stat.hit_rate is None else f"{stat.hit_rate:.0%}"
    ci = f"{stat.wilson[0]:.0%}–{stat.wilson[1]:.0%}" if stat.n else "-"
    mean = "-" if stat.mean_signed is None else f"{stat.mean_signed:+.2f}"
    size = "-" if stat.median_abs is None else f"{stat.median_abs:.2f}"
    flag = "" if stat.enough else " (sampel belum cukup)"
    return f"| {stat.label}{flag} | {stat.n} | {rate} | {ci} | {mean} | {size} |"


def render_markdown(moves: Sequence[EventMove], *, start: datetime, end: datetime) -> str:
    header = "| Grup | n | Arah benar | 95% CI | Rata-rata searah ($) | Median gerak ($) |"
    rule = "|---|---|---|---|---|---|"
    lines = [
        "# Event study",
        "",
        f"Periode {start:%Y-%m-%d} s/d {end:%Y-%m-%d} (UTC) · {len(moves)} berita relevan dengan "
        "harga. Gerak dihitung dari harga saat berita pertama diterima (`first_seen_at`).",
        "",
    ]
    if len(moves) < MIN_SAMPLES:
        lines += [
            f"**Sampel belum cukup** ({len(moves)} < {MIN_SAMPLES}): angka di bawah belum bisa "
            "dipakai untuk kalibrasi.",
            "",
        ]
    sections: list[tuple[str, Callable[[EventMove], str | None], Sequence[int]]] = [
        ("Semua berita", lambda m: "semua", HORIZONS),
        ("Per kategori (+15 mnt)", lambda m: m.category, (15,)),
        ("Per dampak (+15 mnt)", lambda m: f"dampak {m.impact}", (15,)),
        ("Rilis data per event", lambda m: m.event_key, (1, 15, 60)),
        ("Per sumber pertama (+15 mnt)", lambda m: m.source_id, (15,)),
    ]
    for title, key, horizons in sections:
        for horizon in horizons:
            stats = group(moves, key, horizon)
            if not stats:
                continue
            lines += [f"## {title} · +{horizon} mnt", "", header, rule]
            lines += [_row(s) for s in stats]
            lines.append("")
    return "\n".join(lines)

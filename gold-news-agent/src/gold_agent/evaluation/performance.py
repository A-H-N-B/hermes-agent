"""Signal performance from paper-trading outcomes, reported honestly (C3.2).

Win rate carries a 95% Wilson interval, expectancy a bootstrap CI, and small samples are labelled
"sampel belum cukup" instead of being dressed up as results.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from gold_agent.models import Signal, SignalOutcome
from gold_agent.stats import bootstrap_mean_ci, max_drawdown, wilson_interval
from gold_agent.timeutils.sessions import to_wib

MIN_SAMPLES = 30  # below this, rates are shown with an explicit warning
CALIBRATION_SAMPLES = 100  # weights are not re-tuned before this many closed signals
CONFIDENCE_BINS = ((60, 69), (70, 79), (80, 89), (90, 100))


@dataclass(frozen=True)
class GroupStats:
    label: str
    n: int
    wins: int
    win_rate: float | None
    wilson: tuple[float, float]
    avg_r: float | None


@dataclass(frozen=True)
class Performance:
    n_signals: int
    n_open: int
    n_closed: int
    wins: int
    win_rate: float | None
    wilson: tuple[float, float]
    avg_r: float | None
    expectancy_ci: tuple[float, float] | None
    total_r: float
    max_drawdown_r: float
    enough: bool
    notes: list[str] = field(default_factory=lambda: list[str]())
    by_category: list[GroupStats] = field(default_factory=lambda: list[GroupStats]())
    by_session: list[GroupStats] = field(default_factory=lambda: list[GroupStats]())
    by_hour: list[GroupStats] = field(default_factory=lambda: list[GroupStats]())
    by_setup: list[GroupStats] = field(default_factory=lambda: list[GroupStats]())
    calibration: list[GroupStats] = field(default_factory=lambda: list[GroupStats]())


def _group(label: str, rs: Sequence[float]) -> GroupStats:
    wins = sum(r > 0 for r in rs)
    return GroupStats(
        label=label,
        n=len(rs),
        wins=wins,
        win_rate=wins / len(rs) if rs else None,
        wilson=wilson_interval(wins, len(rs)),
        avg_r=sum(rs) / len(rs) if rs else None,
    )


def _grouped(
    pairs: Sequence[tuple[Signal, float]], key: Callable[[Signal], str | None]
) -> list[GroupStats]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for signal, r in pairs:
        buckets[key(signal) or "-"].append(r)
    return [_group(label, rs) for label, rs in sorted(buckets.items())]


def _confidence_bin(signal: Signal) -> str:
    for low, high in CONFIDENCE_BINS:
        if low <= signal.confidence <= high:
            return f"{low}–{high}"
    return f"<{CONFIDENCE_BINS[0][0]}"


def summarize(signals: Sequence[Signal], outcomes: Sequence[SignalOutcome]) -> Performance:
    by_id = {s.signal_id: s for s in signals}
    closed = sorted(
        (o for o in outcomes if o.closed_at is not None and o.r_multiple is not None),
        key=lambda o: o.closed_at or o.opened_at,
    )
    pairs = [(by_id[o.signal_id], o.r_multiple or 0.0) for o in closed if o.signal_id in by_id]
    rs = [r for _, r in pairs]
    overall = _group("semua", rs)
    curve: list[float] = []
    total = 0.0
    for r in rs:
        total += r
        curve.append(total)
    notes: list[str] = []
    enough = len(rs) >= MIN_SAMPLES
    if not enough:
        notes.append(
            f"sampel belum cukup ({len(rs)} sinyal tertutup, butuh ≥ {MIN_SAMPLES}): "
            "angka di bawah belum bermakna statistik"
        )
    if len(rs) < CALIBRATION_SAMPLES:
        notes.append(f"bobot skor belum dikalibrasi (butuh ≥ {CALIBRATION_SAMPLES} sinyal)")
    return Performance(
        n_signals=len(signals),
        n_open=sum(1 for o in outcomes if o.closed_at is None),
        n_closed=len(rs),
        wins=overall.wins,
        win_rate=overall.win_rate,
        wilson=overall.wilson,
        avg_r=overall.avg_r,
        expectancy_ci=bootstrap_mean_ci(rs),
        total_r=round(total, 3),
        max_drawdown_r=round(max_drawdown(curve), 3) if curve else 0.0,
        enough=enough,
        notes=notes,
        by_category=_grouped(pairs, lambda s: s.trigger_category),
        by_session=_grouped(pairs, lambda s: s.session),
        by_hour=_grouped(pairs, lambda s: f"{to_wib(s.created_at_utc).hour:02d}:00"),
        by_setup=_grouped(pairs, lambda s: s.setup_type),
        calibration=_grouped(pairs, _confidence_bin),
    )

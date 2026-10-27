"""Triage evaluation on the labelled headline set (Phase 3 acceptance): relevance precision/recall,
direction accuracy and latency, reported with confidence intervals — never polished."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from gold_agent.evaluation.labels import HeadlineLabel
from gold_agent.stats import percentile, wilson_interval

PRECISION_TARGET = 0.9
LATENCY_TARGET_P95_SECONDS = 5.0
MIN_LABELS = 100


@dataclass(frozen=True)
class Prediction:
    item_id: str
    relevant: bool
    direction: str | None
    impact: int | None
    latency_seconds: float | None = None


@dataclass
class EvalReport:
    name: str
    labelled: int
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    direction_hits: int = 0
    direction_total: int = 0
    confusion: Counter[tuple[str, str]] = field(default_factory=lambda: Counter[tuple[str, str]]())
    impact_abs_errors: list[int] = field(default_factory=lambda: list[int]())
    latencies: list[float] = field(default_factory=lambda: list[float]())

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def direction_accuracy(self) -> float | None:
        return self.direction_hits / self.direction_total if self.direction_total else None

    @property
    def latency_p95(self) -> float | None:
        return percentile(self.latencies, 0.95) if self.latencies else None


def score(
    name: str, labels: Sequence[HeadlineLabel], predictions: Sequence[Prediction]
) -> EvalReport:
    by_id = {p.item_id: p for p in predictions}
    report = EvalReport(name=name, labelled=len(labels))
    for label in labels:
        prediction = by_id.get(label.item_id)
        if prediction is None:
            continue
        if prediction.latency_seconds is not None:
            report.latencies.append(prediction.latency_seconds)
        if label.relevant and prediction.relevant:
            report.tp += 1
        elif prediction.relevant:
            report.fp += 1
        elif label.relevant:
            report.fn += 1
        else:
            report.tn += 1
        if label.relevant and label.direction and prediction.relevant and prediction.direction:
            report.direction_total += 1
            report.direction_hits += label.direction == prediction.direction
            report.confusion[(label.direction, prediction.direction)] += 1
        if label.relevant and label.impact is not None and prediction.impact is not None:
            report.impact_abs_errors.append(abs(label.impact - prediction.impact))
    return report


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%".replace(".", ",")


def render_report(reports: Sequence[EvalReport], *, label_problems: Sequence[str] = ()) -> str:
    lines = ["# Evaluasi Triage (set headline berlabel)", ""]
    if reports and reports[0].labelled < MIN_LABELS:
        lines += [
            f"> ⚠️ Baru {reports[0].labelled} headline berlabel (target ≥ {MIN_LABELS}). "
            "Angka di bawah belum bisa dijadikan dasar keputusan.",
            "",
        ]
    lines += [
        "| Metode | n | Presisi relevansi (95% CI) | Recall | Akurasi arah (95% CI) | MAE dampak "
        "| Latensi p95 | Lolos? |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        p_lo, p_hi = wilson_interval(r.tp, r.tp + r.fp)
        d_lo, d_hi = wilson_interval(r.direction_hits, r.direction_total)
        mae = (
            f"{sum(r.impact_abs_errors) / len(r.impact_abs_errors):.2f}".replace(".", ",")
            if r.impact_abs_errors
            else "—"
        )
        latency = "—" if r.latency_p95 is None else f"{r.latency_p95:.2f} dtk".replace(".", ",")
        precision_ok = r.precision is not None and r.precision >= PRECISION_TARGET
        latency_ok = r.latency_p95 is None or r.latency_p95 < LATENCY_TARGET_P95_SECONDS
        verdict = "✅" if precision_ok and latency_ok and r.labelled >= MIN_LABELS else "❌"
        lines.append(
            f"| {r.name} | {r.labelled} | {_pct(r.precision)} ({_pct(p_lo)}–{_pct(p_hi)}) "
            f"| {_pct(r.recall)} | {_pct(r.direction_accuracy)} ({_pct(d_lo)}–{_pct(d_hi)}) "
            f"| {mae} | {latency} | {verdict} |"
        )
    for r in reports:
        if not r.confusion:
            continue
        lines += ["", f"### Matriks arah — {r.name} (label → prediksi)", ""]
        for (truth, guess), count in sorted(r.confusion.items()):
            lines.append(f"- {truth} → {guess}: {count}")
    if label_problems:
        lines += ["", "### Baris label yang dilewati", ""]
        lines += [f"- {p}" for p in label_problems[:50]]
    lines += [
        "",
        f"Target Fase 3: presisi relevansi ≥ {PRECISION_TARGET:.0%}, p95 triage < "
        f"{LATENCY_TARGET_P95_SECONDS:g} dtk, ≥ {MIN_LABELS} headline berlabel. Akurasi arah "
        "dilaporkan apa adanya.",
        "",
    ]
    return "\n".join(lines)

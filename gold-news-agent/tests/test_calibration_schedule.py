"""Weight calibration (refuses small samples, holdout-validated) and restart-safe job slots."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.schedule import Job, JobLedger
from gold_agent.evaluation.calibration import StateSample, propose_weights, spearman

START = datetime(2026, 9, 1, tzinfo=UTC)


def samples(n: int, *, informative: str, seed: int = 3) -> list[StateSample]:
    rng = random.Random(seed)  # noqa: S311 - test data
    rows: list[StateSample] = []
    for i in range(n):
        comps = {k: rng.uniform(-1, 1) for k in ("N", "M", "T", "P")}
        forward = 4.0 * comps[informative] + rng.gauss(0, 1.0)
        rows.append(
            StateSample(ts=START + timedelta(minutes=10 * i), components=comps, forward=forward)
        )
    return rows


def test_spearman_handles_ties_and_degenerate_input() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([0, 0, 0, 1], [1, 2, 3, 4]) == pytest.approx(0.7746, abs=1e-4)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1, 2], [1, 2]) is None


def test_no_calibration_before_one_hundred_closed_signals(settings: Settings) -> None:
    proposal = propose_weights(
        samples(500, informative="N"), settings.signal_engine.weights, closed_signals=99
    )
    assert not proposal.ready and proposal.proposed is None
    assert proposal.reason == "sampel belum cukup: 99 sinyal tertutup, kalibrasi bobot butuh ≥ 100"


def test_weights_follow_the_component_that_actually_predicts(settings: Settings) -> None:
    proposal = propose_weights(
        samples(600, informative="T"), settings.signal_engine.weights, closed_signals=150
    )
    assert proposal.ready and proposal.proposed is not None
    assert max(proposal.proposed, key=lambda k: proposal.proposed[k]) == "T"  # type: ignore[index]
    assert proposal.proposed["T"] > 0.6
    assert proposal.holdout_ic_proposed is not None and proposal.holdout_ic_current is not None
    assert proposal.holdout_ic_proposed > proposal.holdout_ic_current
    assert (proposal.train_samples, proposal.holdout_samples) == (420, 180)
    assert "technical: " in proposal.yaml_snippet()


def test_noise_is_not_turned_into_new_weights(settings: Settings) -> None:
    rng = random.Random(9)  # noqa: S311 - test data
    noise = [
        StateSample(
            ts=START + timedelta(minutes=i),
            components={k: rng.uniform(-1, 1) for k in ("N", "M", "T", "P")},
            forward=rng.gauss(0, 1),
        )
        for i in range(600)
    ]
    proposal = propose_weights(noise, settings.signal_engine.weights, closed_signals=150)
    assert not proposal.ready  # whatever it fits in training does not survive the holdout


def test_job_slots_and_due() -> None:
    nightly = Job("nightly", "Asia/Jakarta", "05:30")
    before = datetime(2026, 10, 13, 22, 0, tzinfo=UTC)  # 05:00 WIB Wed
    after = datetime(2026, 10, 13, 22, 31, tzinfo=UTC)  # 05:31 WIB Wed
    assert nightly.last_slot(before) == datetime(2026, 10, 12, 22, 30, tzinfo=UTC)
    assert nightly.last_slot(after) == datetime(2026, 10, 13, 22, 30, tzinfo=UTC)
    assert nightly.due(after, None)
    assert nightly.due(after, before) and not nightly.due(after, after)
    weekly = Job("weekly", "Asia/Jakarta", "08:00", "saturday")
    wednesday = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
    assert weekly.last_slot(wednesday) == datetime(2026, 10, 10, 1, 0, tzinfo=UTC)  # Sat 08:00


async def test_ledger_remembers_runs_across_restarts(ctx: AppContext) -> None:
    job = Job("nightly", "Asia/Jakarta", "05:30")
    at = datetime(2026, 10, 13, 22, 31, tzinfo=UTC)
    assert await JobLedger(ctx.ops).last_run(job) is None
    await JobLedger(ctx.ops).mark(job, at)
    assert await JobLedger(ctx.ops).last_run(job) == at

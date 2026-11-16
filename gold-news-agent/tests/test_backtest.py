"""Backtest by replay: real engine + real paper logic, no lookahead, walk-forward plumbing."""

from __future__ import annotations

from datetime import datetime, timedelta

from gold_agent.config import Settings
from gold_agent.evaluation.backtest import (
    Replay,
    render_markdown,
    run_backtest,
    walk_forward,
)
from gold_agent.models import Candle, MarketContext
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, market

START = T0 - timedelta(minutes=30)


def price(minute: int) -> float:
    """Flat-ish before the story at T0, then a steady rally of $0.20 a minute."""
    return 2645 + 0.05 * minute if minute <= 30 else 2646.5 + 0.2 * (minute - 30)


def bars(minutes: int) -> list[Candle]:
    return [
        Candle(
            source="mt5",
            tf="M1",
            open_time=START + timedelta(minutes=m),
            close_time=START + timedelta(minutes=m + 1),
            open=price(m),
            high=max(price(m), price(m + 1)) + 0.05,
            low=min(price(m), price(m + 1)) - 0.05,
            close=price(m + 1),
        )
        for m in range(minutes)
    ]


def contexts(until_minute: int) -> list[MarketContext]:
    return [
        market(
            mid=price(m),
            ts=START + timedelta(minutes=m),
            trend_h4="flat",
            rsi_h1=55.0,
            spread_median_30m=0.2,
        )
        for m in range(0, until_minute)
    ]


def replay_with_late_confirmation() -> tuple[Replay, datetime]:
    confirmed_at = T0 + timedelta(minutes=20)
    rumour = analysis("c1", first_seen=T0, needs_confirmation=True).model_copy(
        update={"version_at": T0 + timedelta(minutes=2)}
    )
    confirmed = analysis("c1", first_seen=T0).model_copy(update={"version_at": confirmed_at})
    replay = Replay(
        contexts=contexts(90),  # snapshots until T0+60
        versions=[rumour, confirmed],
        events=[],
        bars=bars(300),
    )
    return replay, confirmed_at


def test_replay_trades_only_after_the_confirmation_was_published(settings: Settings) -> None:
    replay, confirmed_at = replay_with_late_confirmation()
    result = run_backtest(replay, settings, SessionClock(settings.sessions))
    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.action == "BUY"
    assert signal.created_at_utc == confirmed_at  # never on the unconfirmed version
    (outcome,) = result.outcomes
    assert outcome.status == "tp2" and outcome.r_multiple is not None
    assert 1.3 < outcome.r_multiple < 1.5  # spread + slippage cost something
    assert outcome.opened_at >= signal.created_at_utc


def test_unconfirmed_story_alone_never_trades(settings: Settings) -> None:
    replay, _ = replay_with_late_confirmation()
    replay.versions = replay.versions[:1]  # the confirmation never comes
    assert run_backtest(replay, settings, SessionClock(settings.sessions)).signals == []


def test_walk_forward_scores_each_fold_out_of_sample(settings: Settings) -> None:
    replay, _ = replay_with_late_confirmation()
    folds, combined = walk_forward(
        replay,
        settings,
        SessionClock(settings.sessions),
        folds=2,
        grid=({"entry_threshold": 0.35, "min_confidence": 60},),
    )
    assert len(folds) == 1
    fold = folds[0]
    assert fold.train_expectancy is None  # too few training trades to pick anything
    assert fold.start > replay.contexts[0].ts
    text = render_markdown(folds, combined, start=START, end=START + timedelta(hours=2))
    assert text.startswith("# Backtest walk-forward (replay)")
    assert "Gabungan out-of-sample" in text

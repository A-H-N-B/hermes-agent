"""Backtest by replay (section 17): stored market-context snapshots and every published analysis
version go through the real SignalEngine; trades are managed by the same paper logic as live.

No lookahead: at time t the engine sees only analysis versions published at or before t
(`analysis_versions.version_at`) and contexts recorded at t. Prices between snapshots come from
M1 bars walked conservatively (adverse extreme first). The spread widens around high-impact
releases. Walk-forward picks parameters on earlier folds and scores them on the next, unseen one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from gold_agent.config import Settings
from gold_agent.evaluation.paper import Quote, apply_quote, close_at_market, open_position
from gold_agent.evaluation.performance import Performance, summarize
from gold_agent.market.series import PriceSeries
from gold_agent.models import Analysis, CalendarEvent, Candle, MarketContext, Signal, SignalOutcome
from gold_agent.signals.engine import EngineState, SignalEngine, wib_day
from gold_agent.timeutils.sessions import SessionClock

DEFAULT_SPREAD = 0.30  # $ when a snapshot carries no spread at all
MIN_TRADES_TO_SELECT = 10
GRID: tuple[dict[str, float], ...] = tuple(
    {"entry_threshold": threshold, "min_confidence": confidence}
    for threshold in (0.30, 0.35, 0.40, 0.45)
    for confidence in (55, 60, 70)
)


@dataclass
class Replay:
    contexts: list[MarketContext]
    versions: list[Analysis]  # every published version, oldest first
    events: list[CalendarEvent]
    bars: list[Candle]  # M1 of the snapshots' price source


@dataclass
class RunResult:
    signals: list[Signal] = field(default_factory=lambda: list[Signal]())
    outcomes: list[SignalOutcome] = field(default_factory=lambda: list[SignalOutcome]())

    def performance(self) -> Performance:
        return summarize(self.signals, self.outcomes)


@dataclass(frozen=True)
class FoldResult:
    fold: int
    start: datetime
    end: datetime
    params: dict[str, float]
    train_expectancy: float | None
    test: Performance


def with_params(settings: Settings, params: Mapping[str, float]) -> Settings:
    engine = settings.signal_engine.model_copy(
        update={
            "entry_threshold": float(
                params.get("entry_threshold", settings.signal_engine.entry_threshold)
            ),
            "min_confidence": int(
                params.get("min_confidence", settings.signal_engine.min_confidence)
            ),
        }
    )
    return settings.model_copy(update={"signal_engine": engine})


class _Simulation:
    def __init__(self, replay: Replay, settings: Settings, sessions: SessionClock) -> None:
        self._replay = replay
        self._settings = settings
        self._engine = SignalEngine(settings, sessions, config_version="backtest")
        self._state = EngineState()
        self._prices = PriceSeries.from_candles(replay.bars)
        self._high = sorted(
            e.scheduled_at for e in replay.events if e.impact == "high" and not e.all_day
        )
        self.result = RunResult()
        self._open: dict[str, tuple[Signal, SignalOutcome | None, float]] = {}  # id -> spread
        self._visible: dict[str, Analysis] = {}
        self._version_index = 0
        self._bar_index = 0

    # ------------------------------------------------------------------ prices

    def _spread(self, base: float, moment: datetime) -> float:
        cfg = self._settings.paper
        window = timedelta(minutes=cfg.news_window_minutes)
        near_news = any(abs(moment - at) <= window for at in self._high)
        return base * cfg.news_spread_multiplier if near_news else base

    def _quotes(self, bar: Candle, signal: Signal, spread: float) -> list[Quote]:
        """Conservative path through a bar: open, the adverse extreme, the favourable, close."""
        adverse, favourable = (bar.low, bar.high) if signal.action == "BUY" else (bar.high, bar.low)
        quotes: list[Quote] = []
        for offset, mid in ((0, bar.open), (15, adverse), (30, favourable), (59, bar.close)):
            at = bar.open_time + timedelta(seconds=offset)
            half = self._spread(spread, at) / 2
            quotes.append(Quote(at, round(mid - half, 3), round(mid + half, 3)))
        return quotes

    # ------------------------------------------------------------------ trades

    def _record_close(self, signal: Signal, outcome: SignalOutcome) -> None:
        self.result.outcomes.append(outcome)
        self._open.pop(signal.signal_id, None)
        self._state.closed_signal_ids.add(signal.signal_id)
        closed_at = outcome.closed_at or outcome.opened_at
        day = wib_day(closed_at)
        if self._state.daily_r_day != day:
            self._state.daily_r_day, self._state.daily_r = day, 0.0
        self._state.daily_r += outcome.r_multiple or 0.0

    def _advance(self, until: datetime) -> None:
        bars = self._replay.bars
        while self._bar_index < len(bars) and bars[self._bar_index].close_time <= until:
            bar = bars[self._bar_index]
            self._bar_index += 1
            for signal_id, (signal, outcome, spread) in list(self._open.items()):
                if bar.open_time < signal.created_at_utc:
                    continue
                for quote in self._quotes(bar, signal, spread):
                    if outcome is None:
                        outcome = open_position(signal, quote, self._settings)
                    outcome = apply_quote(outcome, signal, quote, self._settings)
                    if outcome.closed_at is not None:
                        self._record_close(signal, outcome)
                        break
                else:
                    self._open[signal_id] = (signal, outcome, spread)

    def _cancel(self, replaced: Signal, at: datetime) -> None:
        entry = self._open.get(replaced.signal_id)
        if entry is None:
            return
        signal, outcome, spread = entry
        mid = self._prices.at(at)
        if outcome is None or mid is None:
            self._open.pop(signal.signal_id, None)
            return
        half = self._spread(spread, at) / 2
        quote = Quote(at, mid - half, mid + half)
        self._record_close(
            signal, close_at_market(outcome, signal, quote, self._settings, "cancelled")
        )

    # ------------------------------------------------------------------ main loop

    def _visible_analyses(self, now: datetime) -> list[Analysis]:
        versions = self._replay.versions
        while self._version_index < len(versions):
            version = versions[self._version_index]
            if (version.version_at or version.triaged_at) > now:
                break
            self._visible[version.cluster_id] = version
            self._version_index += 1
        horizon = now - timedelta(hours=24)
        return [a for a in self._visible.values() if a.first_seen_at >= horizon]

    def run(self) -> RunResult:
        for context in self._replay.contexts:
            now = context.ts
            self._advance(now)
            decision = self._engine.evaluate(
                now=now,
                context=context,
                analyses=self._visible_analyses(now),
                events=self._replay.events,
                state=self._state,
                price_at=self._prices.at,
            )
            signal = decision.signal
            if signal is None:
                continue
            if decision.replaced is not None:
                self._cancel(decision.replaced, now)
            self._state.record_signal(signal)
            self.result.signals.append(signal)
            base = context.spread_median_30m or context.spread or DEFAULT_SPREAD
            self._open[signal.signal_id] = (signal, None, base)
        if self._replay.bars:
            self._advance(self._replay.bars[-1].close_time)
        return self.result


def run_backtest(replay: Replay, settings: Settings, sessions: SessionClock) -> RunResult:
    return _Simulation(replay, settings, sessions).run()


def _slice(replay: Replay, start: datetime, end: datetime) -> Replay:
    return Replay(
        contexts=[c for c in replay.contexts if start <= c.ts < end],
        versions=replay.versions,
        events=replay.events,
        bars=replay.bars,
    )


def _expectancy(result: RunResult) -> float | None:
    perf = result.performance()
    return perf.avg_r if perf.n_closed >= MIN_TRADES_TO_SELECT else None


def walk_forward(
    replay: Replay,
    settings: Settings,
    sessions: SessionClock,
    *,
    folds: int = 4,
    grid: Sequence[Mapping[str, float]] = GRID,
) -> tuple[list[FoldResult], RunResult]:
    """Expanding window: choose params on folds [0, i), score them on fold i (never seen)."""
    if not replay.contexts or folds < 2:
        return [], RunResult()
    first, last = replay.contexts[0].ts, replay.contexts[-1].ts + timedelta(seconds=1)
    step = (last - first) / folds
    edges = [first + step * i for i in range(folds)] + [last]
    results: list[FoldResult] = []
    combined = RunResult()
    for i in range(1, folds):
        train = _slice(replay, first, edges[i])
        scored: list[tuple[float, dict[str, float]]] = []
        for params in grid:
            expectancy = _expectancy(run_backtest(train, with_params(settings, params), sessions))
            if expectancy is not None:
                scored.append((expectancy, dict(params)))
        best_score, best = max(scored, key=lambda pair: pair[0]) if scored else (None, {})
        test = run_backtest(
            _slice(replay, edges[i], edges[i + 1]), with_params(settings, best), sessions
        )
        combined.signals += test.signals
        combined.outcomes += test.outcomes
        results.append(
            FoldResult(
                fold=i,
                start=edges[i],
                end=edges[i + 1],
                params=best or {"entry_threshold": settings.signal_engine.entry_threshold},
                train_expectancy=best_score,
                test=test.performance(),
            )
        )
    return results, combined


def render_markdown(
    folds: Sequence[FoldResult], combined: RunResult, *, start: datetime, end: datetime
) -> str:
    perf = combined.performance()
    lines = [
        "# Backtest walk-forward (replay)",
        "",
        f"Periode {start:%Y-%m-%d} s/d {end:%Y-%m-%d} (UTC). Parameter dipilih di fold sebelumnya "
        "dan diuji di fold berikutnya yang belum pernah dilihat. Spread & slippage realistis; "
        "spread melebar di sekitar rilis high-impact.",
        "",
        "| Fold | Periode uji | Parameter | Expectancy latih | Sinyal uji | Win rate uji | "
        "Expectancy uji |",
        "|---|---|---|---|---|---|---|",
    ]
    for fold in folds:
        test = fold.test
        params = ", ".join(f"{k}={v:g}" for k, v in fold.params.items())
        train = "-" if fold.train_expectancy is None else f"{fold.train_expectancy:+.2f}R"
        rate = "-" if test.win_rate is None else f"{test.win_rate:.0%}"
        expectancy = "-" if test.avg_r is None else f"{test.avg_r:+.2f}R"
        lines.append(
            f"| {fold.fold} | {fold.start:%Y-%m-%d}–{fold.end:%Y-%m-%d} | {params} | {train} | "
            f"{test.n_closed} | {rate} | {expectancy} |"
        )
    lines += ["", "## Gabungan out-of-sample", ""]
    if perf.n_closed:
        lines.append(
            f"{perf.n_closed} sinyal · win rate {perf.win_rate or 0:.0%} "
            f"(95% CI {perf.wilson[0]:.0%}–{perf.wilson[1]:.0%}) · expectancy "
            f"{perf.avg_r or 0:+.2f}R · total {perf.total_r:+.2f}R · max drawdown "
            f"{perf.max_drawdown_r:.2f}R"
        )
    else:
        lines.append("Tidak ada sinyal tertutup di data uji.")
    lines += [f"- {note}" for note in perf.notes]
    return "\n".join(lines)

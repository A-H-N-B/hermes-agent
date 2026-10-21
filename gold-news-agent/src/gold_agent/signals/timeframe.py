"""Which chart to trade on right now (`trading.timeframe.select: auto`).

Every candidate timeframe (M1 ... H1) is weighed on what can be measured:

* data: its EMA20 and ATR14 must exist;
* cost: the spread next to the stop a trade would use there (1.2 ATR of that timeframe); above
  `max_cost_ratio` the spread eats the trade, so the timeframe is skipped for now;
* evidence: the walk-forward accuracy of the agent's own forecast on that timeframe in the
  current session (signals/forecast_score.py). It counts as proven when, over at least
  MIN_CALLS calls, the lower end of its 95% interval beats a coin flip.

The proven timeframe with the highest lower bound wins. Without one, the session default
(`session_prior`) applies, or the nearest usable timeframe (higher first) when the default
cannot be used. The current timeframe stays unless the winner is clearly better (HYSTERESIS), so
the chart does not flap. Opus 5.5 makes the final pick when it reviews a signal (desk.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from gold_agent.config import TRADE_TIMEFRAMES, Settings, TradingSession
from gold_agent.models import MarketContext
from gold_agent.timeutils.sessions import SESSION_LABEL_ID

JSON = dict[str, Any]
Accuracy = Mapping[str, Mapping[str, JSON]]  # timeframe -> session -> walk-forward tally
KV_TIMEFRAME = "timeframe_choice"  # TfChoice.to_json(), written by the signal service
Basis = Literal["fixed", "data", "prior", "fallback", "none", "opus"]
MIN_CALLS = 40
HYSTERESIS = 0.02  # a challenger needs a lower bound this much higher to replace the current one
RANK = {tf: index for index, tf in enumerate(TRADE_TIMEFRAMES)}
SESSIONS: dict[str, TradingSession] = {
    "asia": "asia",
    "london": "london",
    "overlap": "overlap",
    "new_york": "new_york",
    "closed": "asia",  # a closed market reopens in Asia
}


@dataclass(frozen=True)
class TfOption:
    tf: str
    eligible: bool
    note: str  # why it cannot be used now, or "" when it can
    atr: float | None
    cost_ratio: float | None
    accuracy: JSON | None  # this session's tally: calls, hits, rate, ci95

    @property
    def lower(self) -> float | None:
        ci = (self.accuracy or {}).get("ci95")
        return float(ci[0]) if ci else None

    @property
    def calls(self) -> int:
        return int((self.accuracy or {}).get("calls") or 0)

    def to_json(self) -> JSON:
        return {
            "tf": self.tf,
            "eligible": self.eligible,
            "note": self.note,
            "atr": round(self.atr, 2) if self.atr is not None else None,
            "cost_ratio": round(self.cost_ratio, 3) if self.cost_ratio is not None else None,
            "accuracy": self.accuracy,
        }


@dataclass(frozen=True)
class TfChoice:
    tf: str
    basis: Basis
    reason: str  # one line, Indonesian
    session: str
    options: tuple[TfOption, ...]

    @property
    def eligible(self) -> list[str]:
        return [option.tf for option in self.options if option.eligible]

    def to_json(self) -> JSON:
        return {
            "tf": self.tf,
            "basis": self.basis,
            "reason": self.reason,
            "session": self.session,
            "options": [option.to_json() for option in self.options],
        }


def session_key(session: str) -> TradingSession:
    return SESSIONS.get(session, "asia")


def _option(tf: str, context: MarketContext, tally: JSON | None, settings: Settings) -> TfOption:
    state = context.timeframes.get(tf)
    if state is None or not state.atr14 or state.ema20 is None:
        return TfOption(tf, False, f"data {tf} belum cukup", None, None, tally)
    stop = settings.signal_engine.levels.sl_atr_multiple * state.atr14
    cost = (context.spread or 0.0) / stop
    if cost > settings.trading.timeframe.max_cost_ratio:
        return TfOption(tf, False, f"spread {cost:.0%} dari SL", state.atr14, cost, tally)
    return TfOption(tf, True, "", state.atr14, cost, tally)


def _nearest(prior: str, eligible: list[TfOption]) -> TfOption | None:
    higher = [option for option in eligible if RANK[option.tf] > RANK[prior]]
    if higher:
        return higher[0]
    lower = [option for option in eligible if RANK[option.tf] < RANK[prior]]
    return lower[-1] if lower else None


def _proven_reason(option: TfOption, label: str) -> str:
    tally = option.accuracy or {}
    low, high = tally.get("ci95") or (0.0, 0.0)
    return (
        f"{option.tf}: akurasi sesi {label} {float(tally.get('rate') or 0):.0%} "
        f"(95%: {low:.0%}–{high:.0%}, {option.calls} candle)"
    )


def choose_timeframe(
    *,
    context: MarketContext,
    session: str,
    settings: Settings,
    accuracy: Accuracy,
    current: str | None = None,
) -> TfChoice:
    key = session_key(session)
    label = SESSION_LABEL_ID.get(key, key)
    options = tuple(
        _option(tf, context, (accuracy.get(tf) or {}).get(key), settings) for tf in TRADE_TIMEFRAMES
    )
    select = settings.trading.timeframe.select
    if select != "auto":
        return TfChoice(select, "fixed", f"timeframe tetap {select} (config)", key, options)
    eligible = [option for option in options if option.eligible]
    proven = [
        option
        for option in eligible
        if option.calls >= MIN_CALLS and option.lower is not None and option.lower > 0.5
    ]
    if proven:
        best = max(proven, key=lambda option: option.lower or 0.0)
        kept = next((option for option in proven if option.tf == current), None)
        if kept is not None and (best.lower or 0.0) - (kept.lower or 0.0) < HYSTERESIS:
            best = kept
        return TfChoice(best.tf, "data", _proven_reason(best, label), key, options)
    prior = settings.trading.timeframe.session_prior[key]
    default = next(option for option in options if option.tf == prior)
    if default.eligible:
        reason = f"bawaan sesi {label}; belum ada timeframe yang terbukti lebih akurat"
        return TfChoice(prior, "prior", reason, key, options)
    other = _nearest(prior, eligible)
    if other is not None:
        reason = f"{prior} belum layak ({default.note}), pakai {other.tf}"
        return TfChoice(other.tf, "fallback", reason, key, options)
    return TfChoice(prior, "none", f"belum ada timeframe yang layak ({default.note})", key, options)

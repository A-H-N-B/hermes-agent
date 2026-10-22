"""Peluang TP1 sebelum SL and the expected value of a trade plan, in R.

Two halves, blended the Bayesian way:

1. The volatility model. Price follows Brownian motion with the chart's volatility (GARCH,
   else EWMA, else ATR / 1.6) and a drift that is only non-zero when the agent has *proven*
   an edge on that chart: the lower end of the 95% interval of its walk-forward hit rate p
   gives drift = sigma * InvNorm(p) per bar. Monte Carlo over the signal's validity (antithetic
   paths, barriers checked within each bar) gives TP1 first / stop first / neither. Without a
   proven edge the drift is zero and a 1R target is a coin flip, minus the spread: honest.
2. The record. How past setups on that chart ended (signals/setups.py).

The model counts like PRIOR_STRENGTH past setups (a Dirichlet prior), so with few setups the
model speaks and with many the record does. Expected value: TP1 pays `tp1_fraction` of the
position at its R multiple; the rest runs to TP2 with the stop at breakeven (first-passage
probability), the stop costs 1R, the spread costs spread/risk.
"""

from __future__ import annotations

from typing import Any

from gold_agent.market.quant import drift_from_hit_rate, first_passage, simulate_passage
from gold_agent.models import TimeframeState, TradeOdds
from gold_agent.signals.forecast_score import wilson

JSON = dict[str, Any]
PRIOR_STRENGTH = 20
ATR_TO_SIGMA = 1.6  # a Brownian bar's mean high-low range is ~1.6 sigma


def _volatility(state: TimeframeState | None) -> tuple[float, str] | None:
    if state is None:
        return None
    if state.sigma:
        return state.sigma, state.sigma_model or "garch"
    if state.atr14:
        return state.atr14 / ATR_TO_SIGMA, "atr"
    return None


def _drift(sigma: float, accuracy: JSON | None, tf: str, min_calls: int) -> tuple[float, str]:
    tally = accuracy or {}
    calls, ci = int(tally.get("calls") or 0), tally.get("ci95")
    if calls >= min_calls and ci and float(ci[0]) > 0.5:
        lower = float(ci[0])
        return drift_from_hit_rate(lower, sigma), (
            f"keunggulan {tf} terbukti: batas bawah akurasi {lower:.0%} dari {calls} candle"
        )
    return 0.0, f"belum ada bukti keunggulan di {tf}: arah dianggap acak (drift 0)"


def ev_r(
    tp1: float, stop: float, runner: float, *, r1: float, r2: float, fraction: float, cost_r: float
) -> float:
    """Expected R: TP1 banks `fraction` at r1, the rest reaches r2 with probability `runner`
    (else breakeven); the stop loses 1R; the spread costs cost_r on every trade."""
    return tp1 * (fraction * r1 + (1 - fraction) * r2 * runner) - stop - cost_r


def trade_odds(
    *,
    side: str,
    entry: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    spread: float,
    state: TimeframeState | None,
    tf: str,
    validity_bars: int,
    accuracy: JSON | None,
    min_calls: int,
    tp1_fraction: float,
) -> TradeOdds | None:
    vol = _volatility(state)
    risk = abs(entry - stop_loss)
    reach = abs(tp1 - entry)
    if vol is None or risk <= 0 or reach <= 0:
        return None
    sigma, model = vol
    mu, basis = _drift(sigma, accuracy, tf, min_calls)
    passage = simulate_passage(reach, risk, mu, [sigma**2] * validity_bars)
    runner = first_passage(abs(tp2 - tp1), reach, mu, sigma)
    record = (accuracy or {}).get("setups") or {}
    counts = (int(record.get("tp1", 0)), int(record.get("sl", 0)), int(record.get("expired", 0)))
    n = sum(counts)
    prior = (passage.target, passage.stop, passage.neither)
    blended = [
        (PRIOR_STRENGTH * m + c) / (PRIOR_STRENGTH + n) for m, c in zip(prior, counts, strict=True)
    ]
    p_tp1, p_stop, p_neither = blended
    r1, r2 = reach / risk, abs(tp2 - entry) / risk
    cost_r = spread / risk
    ev = ev_r(p_tp1, p_stop, runner, r1=r1, r2=r2, fraction=tp1_fraction, cost_r=cost_r)
    band = wilson(round(PRIOR_STRENGTH * prior[0] + counts[0]), PRIOR_STRENGTH + n)
    ev_band = None
    if band is not None:
        # Moving probability between TP1 and the stop moves EV the most: the band's two ends.
        low = ev_r(
            band[0],
            p_stop + p_tp1 - band[0],
            runner,
            r1=r1,
            r2=r2,
            fraction=tp1_fraction,
            cost_r=cost_r,
        )
        high = ev_r(
            band[1],
            max(0.0, p_stop - (band[1] - p_tp1)),
            runner,
            r1=r1,
            r2=r2,
            fraction=tp1_fraction,
            cost_r=cost_r,
        )
        ev_band = (round(low, 3), round(high, 3))
    source = f"model + {n} setup historis" if n else "model volatilitas (belum ada setup historis)"
    note = (
        f"Peluang TP1 sebelum SL {p_tp1:.0%} (SL dulu {p_stop:.0%}, tidak tersentuh "
        f"{p_neither:.0%}) · EV {ev:+.2f}R · {source}"
    )
    return TradeOdds(
        tp1=round(p_tp1, 4),
        stop=round(p_stop, 4),
        neither=round(p_neither, 4),
        tp1_ci95=(round(band[0], 4), round(band[1], 4)) if band else None,
        tp2_after_tp1=round(runner, 4),
        ev_r=round(ev, 3),
        ev_r_ci95=ev_band,
        model_tp1=round(passage.target, 4),
        setups=n,
        setups_tp1=counts[0],
        setups_stop=counts[1],
        setups_neither=counts[2],
        drift_basis=basis,
        sigma=round(sigma, 4),
        sigma_model=model,
        horizon_bars=validity_bars,
        cost_r=round(cost_r, 4),
        note=note,
    )

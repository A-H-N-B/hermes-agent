"""Peluang TP1 sebelum SL and the expected value of a trade plan, in R.

Two halves, blended the Bayesian way:

1. The volatility model. Price follows a diffusion with the chart's volatility (GARCH, else
   EWMA, else ATR / 1.6) and a drift that is only non-zero when the agent has *proven* an edge
   on that chart: the lower end of the 95% interval of its walk-forward hit rate p gives
   drift = sigma * InvNorm(p) per bar. When the chart's GARCH-standardised moves have fat
   tails, bars are Student-t (econophysics); when its mean reversion is statistically real
   (Dickey-Fuller), an Ornstein-Uhlenbeck pull towards its mean is added (the Langevin
   equation). Monte Carlo over the signal's validity (20,000 antithetic paths, barriers checked
   within each bar) gives TP1 first / stop first / neither. Without a proven edge or a real pull
   the drift is zero and a 1R target is a coin flip, minus the spread: honest.
2. The record. How past setups on that chart ended (signals/setups.py). Once the meta-labeling
   model (signals/meta.py) has beaten the record's base rate walk-forward, it splits the
   record's resolved setups by this chart's state instead of by their plain frequency.

The model counts like PRIOR_STRENGTH past setups (a Dirichlet prior), so with few setups the
model speaks and with many the record does. Expected value: TP1 pays `tp1_fraction` of the
position at its R multiple; the rest runs to TP2 with the stop at breakeven (first-passage
probability), the stop costs 1R, the spread costs spread/risk.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from gold_agent.market.quant import PATHS, drift_from_hit_rate, first_passage, simulate_passage
from gold_agent.models import TimeframeState, TradeOdds
from gold_agent.signals.forecast_score import wilson
from gold_agent.signals.meta import MIN_TRAIN, features, predict

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


def _meta(
    meta: JSON, state: TimeframeState | None, side: str, at: datetime | None
) -> tuple[float | None, str | None]:
    """The meta-labeling model's P(TP1 first) for this chart state, and a line on whether it
    counts (signals/meta.py)."""
    if not meta:
        return None, None
    samples = int(meta.get("samples") or 0)
    if "weights" not in meta or state is None or at is None:
        return None, f"pembelajaran mesin: baru {samples} setup selesai (butuh {MIN_TRAIN})"
    p = predict(meta, features(state, 1 if side == "BUY" else -1, at))
    skill, t = meta.get("skill"), meta.get("t")
    tested = (
        f"skill walk-forward {skill:+.0%} (t {t:+.1f}, {meta.get('evaluated')} prediksi)"
        if skill is not None and t is not None
        else "belum ada uji walk-forward"
    )
    verdict = "terbukti, dipakai" if meta.get("proven") else "belum terbukti, tidak dipakai"
    return p, (
        f"pembelajaran mesin (meta-labeling dari {samples} setup): P(TP1 dulu) {p:.0%} · "
        f"{tested} → {verdict}"
    )


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
    at: datetime | None = None,
) -> TradeOdds | None:
    vol = _volatility(state)
    risk = abs(entry - stop_loss)
    reach = abs(tp1 - entry)
    if vol is None or risk <= 0 or reach <= 0:
        return None
    sigma, model = vol
    mu, basis = _drift(sigma, accuracy, tf, min_calls)
    nu = state.tail_nu if state else None
    revert = None
    if state and state.ou_half_life and state.ou_mean is not None:
        towards = state.ou_mean - entry if side == "BUY" else entry - state.ou_mean
        revert = (math.log(2) / state.ou_half_life, towards)
    passage = simulate_passage(reach, risk, mu, [sigma**2] * validity_bars, nu=nu, revert=revert)
    runner = first_passage(abs(tp2 - tp1), reach, mu, sigma)
    record = (accuracy or {}).get("setups") or {}
    seen = (int(record.get("tp1", 0)), int(record.get("sl", 0)), int(record.get("expired", 0)))
    n = sum(seen)
    meta = (accuracy or {}).get("meta") or {}
    meta_tp1, meta_note = _meta(meta, state, side, at)
    counts: tuple[float, float, float] = seen
    if meta_tp1 is not None and meta.get("proven"):
        resolved = seen[0] + seen[1]
        counts = (meta_tp1 * resolved, (1 - meta_tp1) * resolved, seen[2])
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
    tails = f"ekor tebal Student-t ν={nu:g}" if nu else "ekor normal"
    pull = (
        f"tarikan mean reversion (OU) ke {state.ou_mean:.2f}, half-life "
        f"{state.ou_half_life:.0f} candle"
        if revert and state and state.ou_mean is not None and state.ou_half_life
        else "tanpa mean reversion yang nyata"
    )
    model_note = (
        f"simulasi {PATHS:,} jalur harga".replace(",", ".")
        + f" · volatilitas {model} {sigma:.2f}$/candle · {tails} · {pull}"
    )
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
        setups_tp1=seen[0],
        setups_stop=seen[1],
        setups_neither=seen[2],
        drift_basis=basis,
        sigma=round(sigma, 4),
        sigma_model=model,
        horizon_bars=validity_bars,
        cost_r=round(cost_r, 4),
        tail_nu=nu,
        revert_half_life=round(state.ou_half_life, 1)
        if revert and state and state.ou_half_life
        else None,
        paths=PATHS,
        model_note=model_note,
        meta_tp1=round(meta_tp1, 4) if meta_tp1 is not None else None,
        meta_note=meta_note,
        note=note,
    )

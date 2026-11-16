"""Paper trade management (section 17), shared by the live paper service and the backtest so a
signal is judged the same way in both.

- Entry: market fill at the first quote after the signal, ask (BUY) / bid (SELL) plus slippage.
- Exits are evaluated on the side we would close at: bid for a BUY, ask for a SELL.
- The stop is checked before targets (conservative). Stop and expiry fills are market orders
  (slippage); targets are limit orders (filled at the level).
- `tp1_fraction` closes at TP1, the stop moves to breakeven, the rest aims for TP2.
- Before TP1 the signal's validity applies; after TP1 the runner gets `max_hold_hours`.
R is measured against the actual fill: risk = |fill - initial stop|.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from gold_agent.config import Settings
from gold_agent.models import Signal, SignalOutcome
from gold_agent.models.signals import OutcomeStatus

MIN_RISK = 0.01  # $; a fill at the stop itself would divide by zero


@dataclass(frozen=True)
class Quote:
    ts: datetime
    bid: float
    ask: float


def _sign(signal: Signal) -> float:
    return 1.0 if signal.action == "BUY" else -1.0


def open_position(signal: Signal, quote: Quote, settings: Settings) -> SignalOutcome:
    slip = settings.paper.slippage_usd
    fill = quote.ask + slip if signal.action == "BUY" else quote.bid - slip
    return SignalOutcome(
        signal_id=signal.signal_id,
        action="BUY" if signal.action == "BUY" else "SELL",
        status="open",
        entry_price=round(fill, 3),
        opened_at=quote.ts,
        risk_per_unit=max(abs(fill - signal.stop_loss), MIN_RISK),
        stop=signal.stop_loss,
        last_price_at=quote.ts,
        price_source=signal.price_source,
    )


def _close(
    outcome: SignalOutcome, at: datetime, price: float, total_r: float, status: OutcomeStatus
) -> SignalOutcome:
    return outcome.model_copy(
        update={
            "status": status,
            "exit_price": round(price, 3),
            "closed_at": at,
            "r_multiple": round(total_r, 3),
            "remaining": 0.0,
            "duration_minutes": round((at - outcome.opened_at).total_seconds() / 60, 1),
        }
    )


def _r(signal: Signal, outcome: SignalOutcome, price: float, fraction: float) -> float:
    risk = outcome.risk_per_unit or MIN_RISK
    return _sign(signal) * (price - outcome.entry_price) / risk * fraction


def close_at_market(
    outcome: SignalOutcome,
    signal: Signal,
    quote: Quote,
    settings: Settings,
    status: OutcomeStatus,
) -> SignalOutcome:
    sign = _sign(signal)
    exit_side = quote.bid if signal.action == "BUY" else quote.ask
    price = exit_side - sign * settings.paper.slippage_usd
    total = outcome.realized_r + _r(signal, outcome, price, outcome.remaining)
    return _close(outcome, quote.ts, price, total, status)


def apply_quote(
    outcome: SignalOutcome, signal: Signal, quote: Quote, settings: Settings
) -> SignalOutcome:
    """Advance an open position by one quote; returns it updated (closed when an exit fired)."""
    if outcome.closed_at is not None or quote.ts < (outcome.last_price_at or outcome.opened_at):
        return outcome
    sign = _sign(signal)
    exit_side = quote.bid if signal.action == "BUY" else quote.ask
    risk = outcome.risk_per_unit or MIN_RISK
    excursion = sign * (exit_side - outcome.entry_price) / risk
    current = outcome.model_copy(
        update={
            "mfe_r": round(max(outcome.mfe_r, excursion), 3),
            "mae_r": round(max(outcome.mae_r, -excursion), 3),
            "last_price_at": quote.ts,
        }
    )
    stop = current.stop if current.stop is not None else signal.stop_loss
    if sign * (exit_side - stop) <= 0:
        status: OutcomeStatus = "breakeven" if current.tp1_hit_at else "sl"
        return close_at_market(current, signal, quote, settings, status)
    if current.tp1_hit_at is None and sign * (exit_side - signal.tp1) >= 0:
        fraction = min(settings.paper.tp1_fraction, current.remaining)
        moved = settings.signal_engine.levels.move_sl_to_breakeven_after_tp1
        current = current.model_copy(
            update={
                "tp1_hit_at": quote.ts,
                "realized_r": round(
                    current.realized_r + _r(signal, current, signal.tp1, fraction), 3
                ),
                "remaining": round(current.remaining - fraction, 6),
                "stop": current.entry_price if moved else stop,
            }
        )
        if current.remaining <= 1e-9:
            return _close(current, quote.ts, signal.tp1, current.realized_r, "tp1")
    if current.tp1_hit_at is not None and sign * (exit_side - signal.tp2) >= 0:
        total = current.realized_r + _r(signal, current, signal.tp2, current.remaining)
        return _close(current, quote.ts, signal.tp2, total, "tp2")
    if current.tp1_hit_at is None and quote.ts >= signal.valid_until_utc:
        return close_at_market(current, signal, quote, settings, "expired")
    max_hold = timedelta(hours=settings.paper.max_hold_hours)
    if current.tp1_hit_at is not None and quote.ts >= current.opened_at + max_hold:
        return close_at_market(current, signal, quote, settings, "tp1")
    return current

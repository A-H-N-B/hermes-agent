"""The life of a signal after it is given. Its levels never change (entry zone, SL, TP1, TP2, TP3);
the price decides what happens to it, checked with every evaluation (each second):

* the price comes into the entry zone ("masuk zona entry");
* TP1, TP2, TP3 are reached; TP3 ends it ("selesai");
* the SL is breached: the signal is INVALID and ends at once, whatever TPs came before;
* no TP1 by `valid_until`: it expires; after TP1 it runs on toward TP3 until SL, TP3 or
  `run_until` (`paper.max_hold_hours` after it was given, the same limit the paper trader uses).

A BUY is judged on the bid and a SELL on the ask, the prices it would close at at the broker; the
entry zone on the mid.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from gold_agent.models import Signal, SignalProgress
from gold_agent.models.signals import ProgressEnd

Event = Literal["entered", "tp1", "tp2", "tp3", "sl", "expired"]


def start(signal: Signal) -> SignalProgress:
    return SignalProgress(signal_id=signal.signal_id, action=signal.action)  # type: ignore[arg-type]


def advance(
    progress: SignalProgress,
    signal: Signal,
    *,
    bid: float,
    ask: float,
    at: datetime,
    max_hold: timedelta,
) -> tuple[SignalProgress, list[Event]]:
    """The progress after this price, and what just happened (in order)."""
    if progress.ended_at is not None:
        return progress, []
    buy = signal.action == "BUY"
    price = bid if buy else ask
    reached = (lambda level: price >= level) if buy else (lambda level: price <= level)
    events: list[Event] = []
    update: dict[str, object] = {}

    def end(why: ProgressEnd, event: Event) -> tuple[SignalProgress, list[Event]]:
        events.append(event)
        final = update | {"ended_at": at, "end": why, "end_price": price}
        return progress.model_copy(update=final), events

    breached = price <= signal.stop_loss if buy else price >= signal.stop_loss
    if breached:
        return end("sl", "sl")
    in_zone = signal.entry_low <= (bid + ask) / 2 <= signal.entry_high
    hit_tp = reached(signal.tp1)
    if progress.entered_at is None and (in_zone or hit_tp):
        update["entered_at"] = at
        events.append("entered")
    for name, level in (("tp1", signal.tp1), ("tp2", signal.tp2), ("tp3", signal.tp3)):
        if getattr(progress, f"{name}_at") is None and reached(level):
            update[f"{name}_at"] = at
            if name == "tp3":
                return end("tp3", "tp3")
            events.append(name)  # type: ignore[arg-type]
            if name == "tp1":
                update["run_until"] = signal.created_at_utc + max_hold
    tp1_done = progress.tp1_at is not None or "tp1_at" in update
    run_until = progress.run_until or update.get("run_until")
    limit = run_until if tp1_done and isinstance(run_until, datetime) else signal.valid_until_utc
    if at >= limit:
        return end("expired", "expired")
    return progress.model_copy(update=update), events


def still_running(progress: SignalProgress | None, signal: Signal, now: datetime) -> bool:
    """Whether the signal is still in play: not ended, and before its expiry (or, past TP1,
    before `run_until`), even when no price came to end it (a closed market)."""
    if progress is None or progress.signal_id != signal.signal_id:
        return now < signal.valid_until_utc
    if progress.ended_at is not None:
        return False
    limit = progress.run_until if progress.tp1_at and progress.run_until else signal.valid_until_utc
    return now < limit

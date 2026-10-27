"""Entry, stop and targets (10.4) plus position size. Contract size is never assumed."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from gold_agent.config import LevelSettings, RiskSettings
from gold_agent.market.levels import nearest_above, nearest_below
from gold_agent.models import Level, MarketContext

Side = Literal["BUY", "SELL"]
SWING_KINDS = ("support", "resistance", "range")


@dataclass(frozen=True)
class TradeLevels:
    entry: float
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    sl_distance: float
    sl_atr_multiple: float
    risk_reward: float
    tp3_source: str


def compute_levels(
    side: Side, context: MarketContext, settings: LevelSettings, tf: str
) -> TradeLevels | None:
    """Entry zone, stop and targets sized on the trade's chart `tf`: its ATR(14), and the stop
    goes beyond its nearest swing when that lies within 3 ATR, else 1.2 ATR from the entry."""
    state = context.timeframes.get(tf)
    if state is None or not state.atr14 or context.bid is None or context.ask is None:
        return None
    if context.mid is None:
        return None
    atr = state.atr14
    sign = 1.0 if side == "BUY" else -1.0
    entry = context.ask if side == "BUY" else context.bid
    swings = [lvl for lvl in context.levels if lvl.kind in SWING_KINDS]
    if side == "BUY" and state.swing_low is not None:
        swings.append(Level(name=f"swing low {tf}", price=state.swing_low, kind="support"))
    if side == "SELL" and state.swing_high is not None:
        swings.append(Level(name=f"swing high {tf}", price=state.swing_high, kind="resistance"))
    if side == "BUY":
        swing = nearest_below(swings, entry, min_distance=0.1 * atr)
    else:
        swing = nearest_above(swings, entry, min_distance=0.1 * atr)
    distance = settings.sl_atr_multiple * atr
    if swing is not None:
        beyond = abs(entry - swing.price) + 0.1 * atr
        if beyond <= 3 * atr:  # a swing further than 3 ATR would make the stop absurdly wide
            distance = max(distance, beyond)
    stop = entry - sign * distance
    r_multiples = list(settings.tp_r_multiples) + [1.0, 2.0][len(settings.tp_r_multiples) :]
    tp1 = entry + sign * r_multiples[0] * distance
    tp2 = entry + sign * r_multiples[1] * distance
    structure: Level | None = (
        nearest_above(context.levels, tp2) if side == "BUY" else nearest_below(context.levels, tp2)
    )
    if structure is not None and abs(structure.price - entry) <= 5 * distance:
        tp3, tp3_source = structure.price, structure.name
    else:
        tp3, tp3_source = entry + sign * 3 * distance, "3R"
    return TradeLevels(
        entry=round(entry, 2),
        entry_low=round(context.mid - 0.1 * atr, 2),
        entry_high=round(context.mid + 0.1 * atr, 2),
        stop_loss=round(stop, 2),
        tp1=round(tp1, 2),
        tp2=round(tp2, 2),
        tp3=round(tp3, 2),
        sl_distance=round(distance, 2),
        sl_atr_multiple=round(distance / atr, 2),
        risk_reward=round(abs(tp2 - entry) / distance, 2),
        tp3_source=tp3_source,
    )


def position_size(
    *,
    sl_distance: float,
    risk: RiskSettings,
    contract_size: float | None,
    volume_step: float | None,
) -> tuple[float | None, str | None]:
    """Lots = (equity × risk%) / (SL distance in $ × contract size), rounded down to the step."""
    if risk.account_equity is None:
        return None, "isi risk.account_equity untuk menghitung lot"
    size = contract_size or risk.contract_size
    if size is None:
        return None, "contract size broker belum diketahui (pakai MT5 atau isi risk.contract_size)"
    if sl_distance <= 0:
        return None, "jarak SL tidak valid"
    risk_money = risk.account_equity * risk.risk_per_trade_pct / 100
    raw = risk_money / (sl_distance * size)
    step = volume_step or risk.volume_step
    lots = math.floor(raw / step + 1e-9) * step
    if lots <= 0:
        return 0.0, f"risiko {risk.risk_per_trade_pct}% terlalu kecil untuk lot minimum {step:g}"
    return round(lots, 4), None

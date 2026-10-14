"""Signal engine (10.3–10.5): turn the composite score into BUY / SELL / WAIT with clear reasons.

Pure decision logic over explicit inputs; the signal service supplies data and persists state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from gold_agent.config import CONFIG_DIR, Settings
from gold_agent.models import Analysis, CalendarEvent, MarketContext, Signal, SignalState
from gold_agent.models.common import Action
from gold_agent.models.signals import SetupType
from gold_agent.signals.levels import TradeLevels, compute_levels, position_size
from gold_agent.signals.scoring import PriceAt, ScoreBreakdown, confidence, score
from gold_agent.timeutils.risky import risky_flags
from gold_agent.timeutils.sessions import SESSION_LABEL_ID, SessionClock, fmt_wib, to_wib

CONFIG_FILES = ("settings.yaml", "gold_drivers.yaml", "keywords.yaml", "events.yaml")
TREND_ID = {"up": "naik", "down": "turun", "flat": "datar"}
KV_ENGINE = "signal_engine"  # EngineState.to_json()
KV_HALT = "trading_halt"  # {"halted": bool, "by": user id, "at": iso}, written by /stop
KEEP_CLOSED_IDS = 200


def config_hash(directory: Path = CONFIG_DIR) -> str:
    digest = hashlib.sha256()
    for name in CONFIG_FILES:
        path = directory / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


@dataclass
class EngineState:
    last_signal: Signal | None = None
    closed_signal_ids: set[str] = field(default_factory=lambda: set[str]())
    signals_day: str | None = None  # WIB date the counter belongs to
    signals_today: int = 0
    last_release_at: datetime | None = None
    last_release_name: str | None = None
    halted: bool = False
    daily_r: float = 0.0  # realised R on `daily_r_day` (paper tracker)
    daily_r_day: str | None = None  # WIB date

    def active(self, now: datetime) -> Signal | None:
        signal = self.last_signal
        if signal is None or signal.signal_id in self.closed_signal_ids:
            return None
        return signal if now < signal.valid_until_utc else None

    def count_today(self, now: datetime) -> int:
        return self.signals_today if self.signals_day == wib_day(now) else 0

    def r_today(self, now: datetime) -> float:
        return self.daily_r if self.daily_r_day == wib_day(now) else 0.0

    def record_signal(self, signal: Signal) -> None:
        day = wib_day(signal.created_at_utc)
        self.signals_today = (self.signals_today if self.signals_day == day else 0) + 1
        self.signals_day = day
        self.last_signal = signal

    def to_json(self) -> dict[str, Any]:
        return {
            "last_signal": self.last_signal.model_dump(mode="json") if self.last_signal else None,
            "closed_signal_ids": sorted(self.closed_signal_ids)[-KEEP_CLOSED_IDS:],
            "signals_day": self.signals_day,
            "signals_today": self.signals_today,
            "last_release_at": self.last_release_at.isoformat() if self.last_release_at else None,
            "last_release_name": self.last_release_name,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EngineState:
        last = data.get("last_signal")
        release_at = data.get("last_release_at")
        return cls(
            last_signal=Signal.model_validate(last) if last else None,
            closed_signal_ids=set(data.get("closed_signal_ids") or []),
            signals_day=data.get("signals_day"),
            signals_today=int(data.get("signals_today") or 0),
            last_release_at=datetime.fromisoformat(release_at) if release_at else None,
            last_release_name=data.get("last_release_name"),
        )


def wib_day(moment: datetime) -> str:
    return to_wib(moment).date().isoformat()


@dataclass(frozen=True)
class Decision:
    state: SignalState
    breakdown: ScoreBreakdown
    signal: Signal | None = None  # a new signal was created
    replaced: Signal | None = None  # the opposite signal this one reverses


def _direction_word(value: float) -> str:
    return "naik" if value > 0 else "turun"


class SignalEngine:
    def __init__(self, settings: Settings, sessions: SessionClock, *, config_version: str) -> None:
        self._settings = settings
        self._sessions = sessions
        self._config_version = config_version

    # ------------------------------------------------------------------ filters

    def wait_reasons(
        self,
        *,
        now: datetime,
        context: MarketContext,
        events: Sequence[CalendarEvent],
        state: EngineState,
        breakdown: ScoreBreakdown,
    ) -> list[str]:
        cfg = self._settings
        engine = cfg.signal_engine
        reasons: list[str] = []
        if state.halted:
            reasons.append("sinyal dihentikan lewat kill switch (/stop)")
        status = self._sessions.status(now)
        if not status.open:
            reasons.append(f"pasar tutup: {status.reason}")
        if context.mid is None:
            reasons.append("data harga tidak tersedia")
        elif context.stale:
            reasons.append(f"data harga basi ({context.tick_age_seconds or 0:.0f} dtk tanpa tick)")
        if context.spread and context.spread > cfg.risk.max_spread_usd:
            reasons.append(
                f"spread melebar ({context.spread:.2f} > batas {cfg.risk.max_spread_usd:.2f})"
            )
        if any(
            "rollover" in flag for flag in risky_flags(now, now, clock=self._sessions, events=[])
        ):
            reasons.append("sekitar rollover harian: likuiditas tipis")
        if not cfg.trading.news_trading:
            window = timedelta(minutes=engine.event_blackout_minutes)
            confirm = timedelta(seconds=cfg.trading.post_release_confirm_seconds)
            for event in events:
                if event.impact != "high" or event.all_day:
                    continue
                if now <= event.scheduled_at <= now + window:
                    minutes = max(0, round((event.scheduled_at - now).total_seconds() / 60))
                    reasons.append(f"rilis {event.country} {event.title} dalam {minutes} mnt")
                elif event.scheduled_at < now <= event.scheduled_at + confirm:
                    reasons.append(f"menunggu konfirmasi harga setelah rilis {event.title}")
            if state.last_release_at and now - state.last_release_at <= confirm:
                reasons.append(
                    f"menunggu konfirmasi harga setelah rilis {state.last_release_name or 'data'}"
                )
        loss_pct = -state.r_today(now) * cfg.risk.risk_per_trade_pct
        if loss_pct >= cfg.risk.max_daily_loss_pct:
            reasons.append(f"batas rugi harian tercapai ({loss_pct:.1f}%)")
        if state.count_today(now) >= cfg.risk.max_signals_per_day:
            reasons.append(f"batas {cfg.risk.max_signals_per_day} sinyal per hari tercapai")
        c = breakdown.components
        threshold = engine.conflict_threshold
        if c.N * c.P < 0 and abs(c.N) >= threshold and abs(c.P) >= threshold:
            reasons.append("arah berita dan pergerakan harga bertentangan kuat")
        return list(dict.fromkeys(reasons))

    # ------------------------------------------------------------------ evaluate

    def evaluate(
        self,
        *,
        now: datetime,
        context: MarketContext,
        analyses: Sequence[Analysis],
        events: Sequence[CalendarEvent],
        state: EngineState,
        price_at: PriceAt,
        reliability: Callable[[Analysis], float] | None = None,
    ) -> Decision:
        engine = self._settings.signal_engine
        weights = engine.weights
        breakdown = score(
            analyses, context, weights=weights, now=now, price_at=price_at, reliability=reliability
        )
        conf = confidence(breakdown, context, weights)
        s = breakdown.composite
        side: Action = "BUY" if s > 0 else "SELL"
        reasons = self.wait_reasons(
            now=now, context=context, events=events, state=state, breakdown=breakdown
        )
        active = state.active(now)

        def wait(extra: list[str]) -> Decision:
            return Decision(
                state=SignalState(
                    ts=now,
                    action=active.action if active and not reasons else "WAIT",
                    composite_score=s,
                    confidence=conf,
                    components=breakdown.components,
                    reasons=self._reasons(breakdown, context),
                    wait_reasons=list(dict.fromkeys([*reasons, *extra])),
                    active_signal_id=active.signal_id if active else None,
                    halted=state.halted,
                ),
                breakdown=breakdown,
            )

        if reasons:
            return wait([])
        if abs(s) < engine.entry_threshold:
            return wait([f"skor gabungan {s:+.2f} di bawah ambang ±{engine.entry_threshold:.2f}"])
        if conf < engine.min_confidence:
            return wait([f"confidence {conf} di bawah {engine.min_confidence}"])
        if abs(breakdown.composite_confirmed_only) < engine.entry_threshold:
            return wait(["menunggu konfirmasi berita (sumber tunggal belum terverifikasi)"])
        replaced: Signal | None = None
        if active is not None:
            if active.action == side:
                return wait([])  # the active signal already says this
            minutes = (now - active.created_at_utc).total_seconds() / 60
            if abs(s) < engine.reversal_threshold or minutes < engine.reversal_min_minutes:
                return wait(
                    [
                        f"pembalikan arah butuh |S| ≥ {engine.reversal_threshold:.2f} dan "
                        f"≥ {engine.reversal_min_minutes} mnt sejak sinyal terakhir"
                    ]
                )
            replaced = active
        last = state.last_signal
        if last is not None:
            since = (now - last.created_at_utc).total_seconds() / 60
            if since < engine.cooldown_minutes:
                return wait([f"cooldown {engine.cooldown_minutes} mnt sejak sinyal terakhir"])
        levels = compute_levels(side, context, engine.levels)
        if levels is None:
            return wait(["level SL/TP belum bisa dihitung (ATR M15 belum tersedia)"])
        signal = self._build_signal(side, now, context, breakdown, conf, levels, events)
        return Decision(
            state=SignalState(
                ts=now,
                action=side,
                composite_score=s,
                confidence=conf,
                components=breakdown.components,
                reasons=signal.top_reasons,
                active_signal_id=signal.signal_id,
                halted=state.halted,
            ),
            breakdown=breakdown,
            signal=signal,
            replaced=replaced,
        )

    # ------------------------------------------------------------------ signal assembly

    def _reasons(self, breakdown: ScoreBreakdown, context: MarketContext) -> list[str]:
        weights = self._settings.signal_engine.weights
        c = breakdown.components
        items: list[tuple[float, str]] = []
        dominant = breakdown.dominant
        if dominant is not None and abs(c.N) > 0.05:
            triage = dominant.analysis.triage
            label = "Berita (belum terkonfirmasi)" if triage.needs_confirmation else "Berita"
            items.append(
                (
                    abs(weights.news * c.N),
                    f"{label}: {triage.summary_id} (dampak {triage.impact_score}/5, "
                    f"{triage.direction_gold})",
                )
            )
        if abs(c.M) > 0.05:
            usd = "melemah" if c.M > 0 else "menguat"
            z = context.dxy_z_1h
            items.append(
                (
                    abs(weights.macro * c.M),
                    f"Makro: USD {usd}" + (f" (z 1 jam {z:+.1f})" if z is not None else ""),
                )
            )
        if abs(c.T) > 0.05:
            h1, h4 = context.timeframes.get("H1"), context.timeframes.get("H4")
            rsi = f"; RSI H1 {h1.rsi14:.0f}" if h1 and h1.rsi14 is not None else ""
            trend_h1 = TREND_ID.get(h1.trend or "", "n/a") if h1 else "n/a"
            trend_h4 = TREND_ID.get(h4.trend or "", "n/a") if h4 else "n/a"
            items.append(
                (
                    abs(weights.technical * c.T),
                    f"Teknikal: tren H1 {trend_h1}, H4 {trend_h4}{rsi}",
                )
            )
        if abs(c.P) > 0.05 and breakdown.price_move is not None:
            anchor = "sejak berita" if dominant is not None else "dalam 45 mnt"
            items.append(
                (
                    abs(weights.price_confirmation * c.P),
                    f"Harga {_direction_word(breakdown.price_move)} "
                    f"${abs(breakdown.price_move):.2f} {anchor}",
                )
            )
        return [text for _, text in sorted(items, key=lambda pair: pair[0], reverse=True)[:3]]

    def _setup_type(
        self, side: Action, breakdown: ScoreBreakdown, context: MarketContext
    ) -> SetupType:
        weights = self._settings.signal_engine.weights
        c = breakdown.components
        sign = 1 if side == "BUY" else -1
        # Only confirmed news can make a setup news-driven (12: never trade needs_confirmation).
        news_part, trend_part = weights.news * breakdown.n_confirmed, weights.technical * c.T
        if abs(news_part) >= abs(trend_part) and news_part * sign > 0:
            return "news_momentum" if c.P * sign >= 0 else "news_fade"
        mid = context.mid or 0.0
        broke_high = context.prev_day_high is not None and mid > context.prev_day_high
        broke_low = context.prev_day_low is not None and mid < context.prev_day_low
        if (side == "BUY" and broke_high) or (side == "SELL" and broke_low):
            return "breakout"
        if trend_part * sign > 0:
            return "trend_continuation"
        return "range"

    def _build_signal(
        self,
        side: Action,
        now: datetime,
        context: MarketContext,
        breakdown: ScoreBreakdown,
        conf: int,
        levels: TradeLevels,
        events: Sequence[CalendarEvent],
    ) -> Signal:
        cfg = self._settings
        validity = timedelta(minutes=cfg.signal_engine.validity_minutes[cfg.trading.style])
        valid_until = now + validity
        lots, lot_note = position_size(
            sl_distance=levels.sl_distance,
            risk=cfg.risk,
            contract_size=context.contract_size,
            volume_step=context.volume_step,
        )
        upcoming = [
            e
            for e in events
            if now <= e.scheduled_at <= now + timedelta(hours=4) and e.impact == "high"
        ]
        invalidation = [
            f"harga menembus SL {levels.stop_loss:.2f}",
            "berita/data baru berdampak ≥ 4 dengan arah berlawanan",
            f"TP1 belum tersentuh sampai {fmt_wib(valid_until)} WIB",
            f"spread melebar di atas {cfg.risk.max_spread_usd:.2f}",
        ]
        invalidation += [
            f"rilis {e.title} pukul {fmt_wib(e.scheduled_at)} WIB bisa membalikkan arah"
            for e in upcoming
            if e.scheduled_at <= valid_until
        ]
        trigger = breakdown.dominant_confirmed
        # The trigger story leads linked_news_ids: latency and attribution follow it.
        ranked = sorted(
            breakdown.contributions,
            key=lambda c: (
                trigger is None or c.analysis.cluster_id != trigger.analysis.cluster_id,
                -abs(c.value),
            ),
        )
        versions = {"config": self._config_version, "engine": "v1"}
        for contribution in ranked[:5]:
            analysis = contribution.analysis
            if analysis.model:
                versions["triage"] = f"{analysis.model}@{analysis.prompt_version or '-'}"
            if analysis.deep_model:
                versions["deep"] = analysis.deep_model
        return Signal(
            signal_id=f"sig-{now:%Y%m%d-%H%M%S}-{side.lower()}",
            created_at_utc=now,
            created_at_wib=f"{fmt_wib(now, with_date=True)} WIB",
            session=SESSION_LABEL_ID.get(context.session, context.session),
            action=side,
            setup_type=self._setup_type(side, breakdown, context),
            entry_type="market",
            entry_low=levels.entry_low,
            entry_high=levels.entry_high,
            stop_loss=levels.stop_loss,
            tp1=levels.tp1,
            tp2=levels.tp2,
            tp3=levels.tp3,
            tp3_source=levels.tp3_source,
            risk_reward=levels.risk_reward,
            risk_pct=cfg.risk.risk_per_trade_pct,
            lot_size=lots,
            lot_note=lot_note,
            valid_until_utc=valid_until,
            valid_until_wib=f"{fmt_wib(valid_until, with_date=True)} WIB",
            confidence=conf,
            composite_score=breakdown.composite,
            components=breakdown.components,
            top_reasons=self._reasons(breakdown, context),
            invalidation=invalidation,
            upcoming_events_4h=[
                f"{fmt_wib(e.scheduled_at)} WIB {e.country} {e.title}"
                + (f" (forecast {e.forecast})" if e.forecast else "")
                for e in upcoming
            ],
            linked_news_ids=[c.analysis.cluster_id for c in ranked[:5]],
            model_versions=versions,
            config_hash=self._config_version,
            price_source=context.source or "-",
            proxy_price=context.proxy,
            bid=context.bid or 0.0,
            ask=context.ask or 0.0,
            spread=context.spread or 0.0,
            sl_distance=levels.sl_distance,
            sl_atr_multiple=levels.sl_atr_multiple,
            first_seen_at=trigger.analysis.first_seen_at if trigger else None,
            trigger_summary=trigger.analysis.triage.summary_id if trigger else None,
            trigger_source=(trigger.analysis.source_ids or ["-"])[0] if trigger else None,
            trigger_category=trigger.analysis.triage.category if trigger else None,
        )

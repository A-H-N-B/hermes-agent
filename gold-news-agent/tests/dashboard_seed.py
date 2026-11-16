"""Realistic dashboard data in a temp database: a live BUY signal, news (one hostile title),
candles, an outlook, a calendar event with its playbook, source health and alerts."""

from __future__ import annotations

from datetime import timedelta

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.econ_calendar.events import load_events
from gold_agent.econ_calendar.playbook import build_playbook
from gold_agent.models import (
    Alert,
    Candle,
    LatencyStage,
    Level,
    Notification,
    SourceHealth,
)
from gold_agent.signals.engine import KV_ENGINE, EngineState, SignalEngine
from gold_agent.signals.outlook import OutlookBuilder, OutlookInputs
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, event, flat_prices, market

NOW = T0 + timedelta(minutes=5)
HOSTILE = '<img src=x onerror="document.body.dataset.pwned=1">Emas naik'
LEVELS = [
    Level(name="swing low H1", price=2644.0, kind="support"),
    Level(name="high kemarin", price=2668.0, kind="resistance"),
]


async def seed_dashboard(ctx: AppContext, settings: Settings) -> None:
    sessions = SessionClock(settings.sessions)
    context = market(ts=NOW, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=LEVELS)
    story = analysis("c1", first_seen=T0)
    hostile = analysis(
        "c2",
        first_seen=T0 - timedelta(minutes=30),
        summary_id=HOSTILE,
        impact_score=3,
        direction_gold="bearish",
    )
    for item in (story, hostile):
        await ctx.news.save_analysis(item)
    engine = SignalEngine(settings, sessions, config_version="seed")
    decision = engine.evaluate(
        now=NOW,
        context=context,
        analyses=[story],
        events=[],
        state=EngineState(),
        price_at=flat_prices(2645.0),
    )
    signal = decision.signal
    assert signal is not None
    state = EngineState()
    state.record_signal(signal)
    await ctx.ops.kv_set(KV_ENGINE, state.to_json(), NOW)
    await ctx.signals.save_signal(signal)
    await ctx.signals.save_state(decision.state)
    await ctx.market.save_context(context)

    start = NOW - timedelta(minutes=5 * 60)
    bars = [
        Candle(
            source="mt5",
            tf="M5",
            open_time=start + timedelta(minutes=5 * i),
            close_time=start + timedelta(minutes=5 * (i + 1)),
            open=2640 + i * 0.1,
            high=2641.5 + i * 0.1,
            low=2639.2 + i * 0.1,
            close=2640.8 + i * 0.1,
        )
        for i in range(60)
    ]
    quarters = [  # the same hours as M15 bars: the New York trading window's chart
        bars[i].model_copy(
            update={
                "tf": "M15",
                "close_time": bars[i + 2].close_time,
                "high": max(b.high for b in bars[i : i + 3]),
                "low": min(b.low for b in bars[i : i + 3]),
                "close": bars[i + 2].close,
            }
        )
        for i in range(0, 60, 3)
    ]
    await ctx.market.upsert_candles(bars + quarters)

    cpi = event(at=NOW + timedelta(hours=2))
    await ctx.calendar.upsert_events([cpi], NOW)
    spec = load_events().by_key("us_cpi_mom")
    assert spec is not None
    await ctx.calendar.save_playbook(
        build_playbook(cpi, spec, surprise_std=0.1, z_basis="prior", now=NOW)
    )
    outlook = OutlookBuilder(settings, sessions, load_events()).build(
        OutlookInputs(
            now=NOW,
            trigger="pembukaan sesi New York",
            context=context,
            state=decision.state,
            active=signal,
            events=[cpi],
        )
    )
    await ctx.signals.save_outlook(outlook)

    await ctx.ops.upsert_source_health(
        SourceHealth(
            source_id="fed_press_all",
            status="ok",
            last_success_at=NOW,
            last_item_at=T0,
            items_total=12,
            updated_at=NOW,
        )
    )
    await ctx.ops.upsert_source_health(
        SourceHealth(
            source_id="bls_latest",
            status="down",
            consecutive_errors=4,
            last_error="HTTP 403",
            last_error_at=NOW,
            updated_at=NOW,
        )
    )
    await ctx.ops.record_latency(
        LatencyStage(
            ref_id="c1",
            source_id="fed_press_all",
            stage="published",
            ts=T0,
            seconds_from_first_seen=-4.0,
        )
    )
    await ctx.ops.record_latency(
        LatencyStage(
            ref_id="c1", source_id="wire", stage="signal", ts=NOW, seconds_from_first_seen=300.0
        )
    )
    await ctx.ops.save_alert(
        Alert(
            id="a1",
            kind="health",
            severity="warning",
            title="Sumber bls_latest mati",
            detail="4 error beruntun (HTTP 403).",
            created_at=NOW,
            dedupe_key="src:bls",
        )
    )
    await ctx.notify.save(
        Notification(id="n1", kind="signal", priority="high", text="x", created_at=NOW, sent_at=NOW)
    )
    await ctx.ops.heartbeat("signal", NOW)

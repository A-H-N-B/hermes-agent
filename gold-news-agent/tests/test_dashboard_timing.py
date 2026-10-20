"""Live page 'Kapan klik BUY/SELL' and the indicator read: what the user is told to do, and when."""

from __future__ import annotations

from datetime import timedelta

from gold_agent.config import Settings
from gold_agent.dashboard.indicators_view import (
    WARMUP_BARS,
    indicator_series,
    indicator_summary,
    timeframe_read,
)
from gold_agent.dashboard.timing import bias_action, timing_view
from gold_agent.models import Level, Outlook, OutlookRow, TimeframeState
from tests.builders import T0, event, market
from tests.test_notify_templates import make_signal
from tests.test_replay_bridge import history


def _row(hours: int, bias: str, *, conditional: bool = False) -> OutlookRow:
    start = T0.replace(minute=0) + timedelta(hours=hours)
    return OutlookRow(
        hour_start_utc=start,
        label_wib=f"{(start.hour + 7) % 24:02d}:00",
        session="London",
        events=[],
        expected_range_usd=6.0,
        volatility_label="sedang",
        bias=bias,
        scenario=f"skenario {bias}",
        confidence=40,
        risky=[],
        conditional=conditional,
    )


def _outlook(*rows: OutlookRow) -> Outlook:
    return Outlook(id="o", created_at=T0, trigger="hourly", rows=list(rows))


def test_outlook_bias_text_maps_to_the_button_to_press() -> None:
    assert [bias_action(b) for b in ("BUY", "Condong naik", "Bersyarat (condong turun)")] == [
        "BUY",
        "BUY",
        "SELL",
    ]
    assert [bias_action(b) for b in ("Netral", "TUNGGU DATA", "PASAR TUTUP", "Bersyarat")] == [
        None
    ] * 4


def test_an_active_signal_says_act_now_and_until_when(settings: Settings) -> None:
    signal = make_signal(settings)
    now = signal.created_at_utc + timedelta(minutes=1)
    view = timing_view(
        now=now, settings=settings, active=signal, state=None, outlook=None, events=[], context=None
    )
    assert view["now"]["mode"] == "signal" and view["now"]["action"] == "BUY"
    assert view["now"]["until_utc"] == signal.valid_until_utc.isoformat()
    assert (view["now"]["entry_low"], view["now"]["stop_loss"]) == (
        signal.entry_low,
        signal.stop_loss,
    )


def test_waiting_lists_the_next_buy_and_sell_hours_and_the_no_trade_window(
    settings: Settings,
) -> None:
    release = event("CPI m/m", at=T0 + timedelta(hours=2))
    minor = event("Speech", at=T0 + timedelta(hours=1), impact="medium", key=None)
    outlook = _outlook(
        _row(-1, "Condong naik"),  # already over: never offered
        _row(0, "BUY"),  # the active signal's own hour: shown as "now", not as a window
        _row(1, "Condong naik"),
        _row(2, "TUNGGU DATA"),
        _row(3, "PASAR TUTUP"),
        _row(4, "Bersyarat (condong turun)", conditional=True),
    )
    context = market(
        2650.0,
        levels=[
            Level(name="R1", price=2658.0, kind="resistance"),
            Level(name="S1", price=2644.0, kind="support"),
        ],
    )
    view = timing_view(
        now=T0,
        settings=settings,
        active=None,
        state=None,
        outlook=outlook,
        events=[minor, release],
        context=context,
    )
    assert view["now"]["mode"] == "wait"
    assert [(w["action"], w["conditional"]) for w in view["windows"]] == [
        ("BUY", False),
        ("SELL", True),
    ]
    assert view["windows"][0]["from_utc"] == (T0.replace(minute=0) + timedelta(hours=1)).isoformat()
    blackout = view["blackout"]
    minutes = settings.signal_engine.event_blackout_minutes
    assert blackout["title"] == "USD CPI m/m"  # the high-impact one, not the nearer speech
    assert blackout["from_utc"] == (release.scheduled_at - timedelta(minutes=minutes)).isoformat()
    assert blackout["active"] is False
    assert view["triggers"]["buy_above"] == {"price": 2658.0, "name": "R1"}
    assert view["triggers"]["sell_below"] == {"price": 2644.0, "name": "S1"}


def test_the_no_trade_window_stays_on_until_the_post_release_confirmation(
    settings: Settings,
) -> None:
    release = event("NFP", at=T0)
    after = T0 + timedelta(seconds=settings.trading.post_release_confirm_seconds - 1)
    view = timing_view(
        now=after,
        settings=settings,
        active=None,
        state=None,
        outlook=None,
        events=[release],
        context=None,
    )
    assert view["blackout"]["active"] is True


def test_timeframe_read_votes_trend_emas_and_rsi() -> None:
    bull = TimeframeState(
        tf="H1", close=2660, ema20=2655, ema50=2650, ema200=2600, rsi14=62, trend="up"
    )
    bear = TimeframeState(
        tf="M5", close=2640, ema20=2645, ema50=2650, ema200=2700, rsi14=38, trend="down"
    )
    mixed = TimeframeState(tf="M15", close=2650, ema20=2649, ema50=2651, rsi14=50, trend="flat")
    assert timeframe_read(bull)["verdict"] == "BUY KUAT"
    assert timeframe_read(bear)["verdict"] == "SELL KUAT"
    assert timeframe_read(mixed)["verdict"] == "NETRAL"


def test_summary_counts_timeframes_and_lists_levels_with_session_extremes() -> None:
    context = market(
        2650.0,
        levels=[Level(name="R1", price=2658.0, kind="resistance")],
        asia_high=2655.0,
        prev_day_low=2630.0,
    )
    summary = indicator_summary(context)
    assert [r["tf"] for r in summary["timeframes"]] == ["M15", "H1", "H4"]
    assert {lvl["name"] for lvl in summary["levels"]} == {"R1", "High Asia", "Low kemarin"}
    assert summary["nearest_above"] == {"name": "R1", "price": 2658.0}
    assert indicator_summary(None)["timeframes"] == []


def test_overlays_have_ema200_from_the_first_shown_bar_given_the_warmup() -> None:
    bars = history("M5", T0, 300 + WARMUP_BARS)
    rows = indicator_series(bars, 300)
    assert len(rows) == 300 and rows[-1]["time"] == int(bars[-1].open_time.timestamp())
    assert all(r["ema200"] is not None and r["rsi14"] is not None for r in rows)
    assert rows[-1]["bb_lower"] < rows[-1]["bb_mid"] < rows[-1]["bb_upper"]
    assert indicator_series(bars[:10], 10)[0]["ema20"] is None  # not enough data -> no value

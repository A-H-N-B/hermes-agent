"""Trading windows (`trading.windows`): BUY/SELL only in the user's hours, sized, timed and valid
on the window's chart; the timing board shows the window and offers only hours the user trades."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gold_agent.config import Settings, TradingWindow
from gold_agent.dashboard.timing import timing_view
from gold_agent.models import Level, MarketContext, Outlook, OutlookRow, TimeframeState
from gold_agent.notify.templates import render_signal
from gold_agent.signals.engine import Decision, EngineState, SignalEngine
from gold_agent.timeutils.sessions import SessionClock
from gold_agent.timeutils.windows import TradingHours
from tests.builders import T0, analysis, flat_prices, market

LONDON = TradingWindow(name="London", start="14:00", end="17:00", timeframe="M5")
NEW_YORK = TradingWindow(name="New York", start="19:00", end="23:00", timeframe="M15")
NOW = T0 + timedelta(minutes=5)  # Tuesday 19:35 WIB: inside the New York window
LEVELS = [
    Level(name="swing low H1", price=2644.0, kind="support"),
    Level(name="high kemarin", price=2668.0, kind="resistance"),
]


def with_windows(settings: Settings, *windows: TradingWindow) -> Settings:
    trading = settings.trading.model_copy(update={"windows": windows or (LONDON, NEW_YORK)})
    return settings.model_copy(update={"trading": trading})


def hours(settings: Settings, *windows: TradingWindow) -> TradingHours:
    return TradingHours(windows or (LONDON, NEW_YORK), SessionClock(settings.sessions))


def wib(day: int, hhmm: str) -> datetime:
    """October 2026, WIB wall clock -> UTC."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC) - timedelta(hours=7)


def charted(ts: datetime, **frames: TimeframeState) -> MarketContext:
    """The bullish test market, plus the given window-timeframe states."""
    base = market(ts=ts, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=LEVELS)
    return base.model_copy(update={"timeframes": base.timeframes | frames})


def frame(tf: Any, *, ema20: float | None, atr: float, **extra: Any) -> TimeframeState:
    return TimeframeState(tf=tf, ema20=ema20, atr14=atr, trend="up", bars=200, **extra)


def leaning(hour_wib: int, bias: str) -> OutlookRow:
    start = wib(13, f"{hour_wib:02d}:00")
    return OutlookRow(
        hour_start_utc=start,
        label_wib=f"{hour_wib:02d}:00",
        session="London",
        events=[],
        expected_range_usd=6.0,
        volatility_label="sedang",
        bias=bias,
        scenario=f"skenario {bias}",
        confidence=40,
        risky=[],
        conditional=False,
    )


def decide(settings: Settings, now: datetime, context: MarketContext) -> Decision:
    engine = SignalEngine(settings, SessionClock(settings.sessions), config_version="cfg")
    return engine.evaluate(
        now=now,
        context=context,
        analyses=[analysis(first_seen=now - timedelta(minutes=5))],
        events=[],
        state=EngineState(),
        price_at=flat_prices(2645.0),
    )


# ------------------------------------------------------------------ which window is open


def test_the_open_window_and_the_next_one(settings: Settings) -> None:
    clock = hours(settings)
    now = wib(13, "19:30")
    current = clock.current(now)
    assert current is not None and current.label == "New York M15 19:00–23:00 WIB"
    upcoming = clock.next(now)
    assert upcoming is not None and upcoming.start == wib(14, "14:00")
    assert upcoming.label_from(now) == "London M5 Rabu 14 Okt 14:00–17:00 WIB"
    assert clock.current(wib(13, "17:30")) is None
    assert clock.summary(now) == "London M5 14:00–17:00, New York M15 19:00–23:00 WIB"


def test_after_friday_night_the_next_window_is_monday_not_the_weekend(settings: Settings) -> None:
    upcoming = hours(settings).next(wib(16, "23:30"))  # Friday after the New York window
    assert upcoming is not None
    assert (upcoming.name, upcoming.start) == ("London", wib(19, "14:00"))


def test_a_window_set_in_london_time_follows_british_dst(settings: Settings) -> None:
    london_open = TradingWindow(
        name="London open", zone="Europe/London", start="08:00", end="11:00", timeframe="M5"
    )
    clock = hours(settings, london_open)
    friday, monday = clock.next(wib(23, "00:00")), clock.next(wib(26, "00:00"))
    assert friday is not None and friday.start == wib(23, "14:00")  # BST: UTC+1
    assert monday is not None and monday.start == wib(26, "15:00")  # GMT after 25 October


def test_a_window_can_run_past_midnight(settings: Settings) -> None:
    late = TradingWindow(name="Late", start="22:00", end="02:00", timeframe="M15")
    current = hours(settings, late).current(wib(14, "01:00"))
    assert current is not None
    assert (current.start, current.end) == (wib(13, "22:00"), wib(14, "02:00"))


def test_config_refuses_an_empty_window() -> None:
    with pytest.raises(ValueError, match="start and end are the same"):
        TradingWindow(name="X", start="14:00", end="14:00", timeframe="M5")


# ------------------------------------------------------------------ the engine in and out of hours


def test_outside_trading_hours_the_engine_waits_and_says_when_trading_resumes(
    settings: Settings,
) -> None:
    now = wib(13, "17:30")
    decision = decide(with_windows(settings), now, charted(now))
    assert decision.signal is None and decision.state.action == "WAIT"
    assert (
        "di luar jam trading Anda (London M5 14:00–17:00, New York M15 19:00–23:00 WIB); "
        "berikutnya New York M15 19:00–23:00 WIB"
    ) in decision.state.wait_reasons


def test_inside_a_window_the_trade_is_sized_and_valid_on_its_chart(settings: Settings) -> None:
    m15 = frame("M15", ema20=2648.0, atr=4.0, swing_low=2645.0)
    decision = decide(with_windows(settings), NOW, charted(NOW, M15=m15))
    signal = decision.signal
    assert signal is not None and signal.action == "BUY"
    assert (signal.timeframe, signal.trading_window) == ("M15", "New York M15 19:00–23:00 WIB")
    # Stop beyond the nearest M15 swing (2645.00 - 0.1 ATR): 5.50 below 2650.10, wider than 1.2 ATR.
    assert (signal.stop_loss, signal.sl_distance) == (2644.6, 5.5)
    assert (signal.tp1, signal.tp2) == (2655.6, 2661.1)
    assert signal.valid_until_utc == NOW + timedelta(minutes=90)  # 6 candles of M15
    assert "candle M15 ditutup kembali di bawah EMA20 M15 (2648.00)" in signal.invalidation
    text = render_signal(signal, now=NOW, next_event=None)
    assert "📊 Chart M15 · jam trading New York M15 19:00–23:00 WIB" in text
    assert "🛑 SL 2644.60 (5.50$ · 1.37×ATR M15)" in text


def test_an_m5_window_sizes_a_tighter_trade_valid_for_30_minutes(settings: Settings) -> None:
    scalp = TradingWindow(name="Scalp", start="19:00", end="23:00", timeframe="M5")
    m5 = frame("M5", ema20=2649.0, atr=2.0)
    signal = decide(with_windows(settings, scalp), NOW, charted(NOW, M5=m5)).signal
    assert signal is not None and signal.timeframe == "M5"
    assert (signal.entry_low, signal.entry_high) == (2649.8, 2650.2)  # mid ± 0.1 ATR(M5)
    assert (signal.stop_loss, signal.sl_distance) == (2647.7, 2.4)  # 1.2 ATR(M5)
    assert signal.valid_until_utc == NOW + timedelta(minutes=30)


@pytest.mark.parametrize(
    ("ema20", "reason"),
    [
        (2652.0, "timing M15: tunggu harga kembali di atas EMA20 M15 (2652.00)"),
        (2640.0, "timing M15: harga sudah 2.5 ATR dari EMA20 M15 (2640.00); tunggu pullback"),
    ],
)
def test_the_entry_is_timed_on_the_window_chart(
    settings: Settings, ema20: float, reason: str
) -> None:
    m15 = frame("M15", ema20=ema20, atr=4.0)
    decision = decide(with_windows(settings), NOW, charted(NOW, M15=m15))
    assert decision.signal is None
    assert any(text.startswith(reason) for text in decision.state.wait_reasons)


def test_without_the_window_chart_the_engine_waits_for_its_data(settings: Settings) -> None:
    decision = decide(
        with_windows(settings), NOW, charted(NOW, M15=frame("M15", ema20=None, atr=4))
    )
    assert "menunggu data M15 (EMA20/ATR M15 belum tersedia)" in decision.state.wait_reasons


# ------------------------------------------------------------------ the timing board


def test_the_board_shows_the_window_and_offers_only_hours_the_user_trades(
    settings: Settings,
) -> None:
    now = wib(13, "17:30")
    rows = [
        leaning(17, "Condong naik"),  # outside both windows
        leaning(18, "Condong turun"),  # outside
        leaning(19, "Condong naik"),  # the New York window
    ]
    outlook = Outlook(id="o", created_at=now, trigger="hourly", rows=rows)
    view = timing_view(
        now=now,
        settings=with_windows(settings),
        active=None,
        state=None,
        outlook=outlook,
        events=[],
        context=None,
    )
    trading = view["trading"]
    assert trading["active"] is None
    assert trading["next"]["label"] == "New York M15 19:00–23:00 WIB"
    assert [(w["action"], w["label_wib"], w["timeframe"]) for w in view["windows"]] == [
        ("BUY", "19:00–20:00", "M15")
    ]
    inside = timing_view(
        now=NOW,
        settings=with_windows(settings),
        active=None,
        state=None,
        outlook=None,
        events=[],
        context=None,
    )
    assert inside["trading"]["active"]["timeframe"] == "M15"

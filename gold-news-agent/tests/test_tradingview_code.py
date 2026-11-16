"""The TradingView signal code: what the Telegram message and dashboard hand to the user, and what
the "Gold News Agent" Pine indicator parses back into levels on the chart."""

from __future__ import annotations

from pathlib import Path

from gold_agent.config import Settings
from gold_agent.dashboard.app import STATIC_DIR
from gold_agent.notify.templates import render_signal
from gold_agent.signals.tvcode import CODE_VERSION, tradingview_code
from tests.test_notify_templates import NOW, make_signal

PINE = STATIC_DIR / "gold_news_agent.pine"


def test_the_code_carries_every_level_the_indicator_draws(settings: Settings) -> None:
    signal = make_signal(settings)
    fields = tradingview_code(signal).split("|")
    assert fields[:2] == [CODE_VERSION, "BUY"]
    assert int(fields[2]) == int(signal.created_at_utc.timestamp())
    assert float(fields[3]) == round((signal.bid + signal.ask) / 2, 2)  # anchor for the shift
    levels = [float(f) for f in fields[4:10]]
    expected = (signal.entry_low, signal.entry_high, signal.stop_loss, signal.tp1, signal.tp2)
    assert levels == [round(v, 2) for v in (*expected, signal.tp3)]
    assert int(fields[10]) == int(signal.valid_until_utc.timestamp())
    assert fields[11:] == [str(signal.confidence), "P" if signal.proxy_price else "B"]


def test_telegram_shows_the_code_as_one_tap_to_copy_block(settings: Settings) -> None:
    signal = make_signal(settings)
    text = render_signal(signal, now=NOW, next_event=None)
    assert f"<code>{tradingview_code(signal)}</code>" in text


def test_the_shipped_indicator_reads_this_code_version_and_field_count(
    settings: Settings,
) -> None:
    # The Pine script cannot be executed here; this pins the two ends of the contract together.
    script = Path(PINE).read_text(encoding="ascii")  # ASCII: pasted into TradingView verbatim
    fields = tradingview_code(make_signal(settings)).split("|")
    assert f"array.size(bagian) == {len(fields)}" in script
    assert f'array.get(bagian, 0) == "{CODE_VERSION}"' in script
    assert "strategy(" not in script and "strategy.entry" not in script  # draws, never trades

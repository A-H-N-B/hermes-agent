"""Telegram templates (section 15): content, escaping of untrusted text, and the 4096 limit."""

from __future__ import annotations

from datetime import timedelta

import pytest

from gold_agent.config import Settings
from gold_agent.econ_calendar.events import load_events
from gold_agent.econ_calendar.playbook import build_playbook
from gold_agent.models import Level, ReleaseReading, Signal, SignalReview
from gold_agent.notify.format import TELEGRAM_LIMIT, split_message, strip_tags
from gold_agent.notify.templates import (
    render_flash,
    render_news,
    render_outlook,
    render_playbook,
    render_signal,
)
from gold_agent.signals.engine import EngineState, SignalEngine
from gold_agent.signals.outlook import OutlookBuilder, OutlookInputs
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, event, flat_prices, market

NOW = T0 + timedelta(minutes=5)
LEVELS = [
    Level(name="swing low H1", price=2644.0, kind="support"),
    Level(name="high kemarin", price=2668.0, kind="resistance"),
]


def make_signal(settings: Settings, **story: object) -> Signal:
    engine = SignalEngine(settings, SessionClock(settings.sessions), config_version="cfg")
    decision = engine.evaluate(
        now=NOW,
        context=market(ts=NOW, dxy_z_1h=-1.5, dxy_z_15m=-1.0, levels=LEVELS),
        analyses=[analysis(first_seen=T0, **story)],  # type: ignore[arg-type]
        events=[],
        state=EngineState(),
        price_at=flat_prices(2645.0),
    )
    assert decision.signal is not None
    return decision.signal


def test_signal_message_follows_the_template(settings: Settings) -> None:
    signal = make_signal(settings)
    release = event("Retail Sales m/m", at=NOW + timedelta(hours=2), key="us_retail_sales_mom")
    text = render_signal(signal, now=NOW, next_event=release)
    lines = text.split("\n")
    assert lines[0] == "🟢 <b>BUY XAUUSD</b> | Confidence 100/100"
    assert lines[1] == "🕐 19:35 WIB · Sesi London–New York"
    assert lines[2] == (
        "📊 Chart M15 (bawaan sesi London–New York; belum ada timeframe yang terbukti lebih akurat)"
    )
    assert lines[3] == "💵 Harga 2649.90/2650.10 · Spread 0.20"
    assert lines[4] == "🎯 Entry 2649.60–2650.40 (market)"
    assert lines[5] == "🛑 SL 2643.60 (6.50$ · 1.62×ATR M15)"
    assert lines[6] == "✅ TP1 2656.60 (1R) · TP2 2663.10 (2R) · TP3 2668.00 (high kemarin)"
    assert lines[7] == "⏳ Berlaku s/d Selasa 13 Okt 21:05 WIB"  # 6 candles of M15
    assert lines[8] == (
        "📰 Pemicu: Serangan rudal memicu permintaan aset aman. — wire (diterima 300 dtk)"
    )
    assert lines[9].startswith("📊 Berita +0.66 · Makro +0.57 · Teknikal +0.62")
    assert any(line.startswith("⚠️ Batal jika: harga menembus SL 2643.60") for line in lines)
    assert "📅 Event berikutnya: USD Retail Sales m/m pukul 21:35 WIB (2 jam lagi)" in lines
    assert any("isi risk.account_equity" in line for line in lines)  # no guessed lot size
    assert lines[-1] == "<i>Bukan saran keuangan. Keputusan &amp; risiko di tanganmu.</i>"


def test_an_opus_decision_shows_its_vote_count_and_the_dissent(settings: Settings) -> None:
    review = SignalReview(
        model="claude-opus-5-5",
        confidence=74,
        timeframe="M15",
        reasons=["berita & chart M15 searah"],
        caution="CPI <besok>",
        votes=3,
        agree=2,
        dissent="harga sudah jauh dari EMA20",
    )
    signal = make_signal(settings).model_copy(update={"review": review})
    lines = render_signal(signal, now=NOW, next_event=None).split("\n")
    assert lines[3] == (
        "🤖 Diputuskan Opus 5.5 · 2/3 suara · confidence AI 74/100 · berita &amp; chart M15 searah"
    )
    assert lines[4] == "⚠️ Catatan AI: CPI &lt;besok&gt;"
    assert lines[5] == "🗳️ Suara yang menahan: harga sudah jauh dari EMA20"


def test_untrusted_news_text_is_escaped(settings: Settings) -> None:
    hostile = "<b>Gold</b> & <a href='x'>klik</a>"
    signal = make_signal(settings, summary_id=hostile)
    assert "&lt;b&gt;Gold&lt;/b&gt; &amp; &lt;a href='x'&gt;klik&lt;/a&gt;" in render_signal(
        signal, now=NOW, next_event=None
    )
    news = render_news(analysis(summary_id=hostile, impact_score=4), now=NOW)
    assert "<a href" not in news and "&lt;a href='x'&gt;" in news
    assert news.startswith("🔴 <b>BERITA PENTING</b> · dampak 4/5 · bullish · geopolitics")


def test_flash_shows_surprise_bucket_and_latency() -> None:
    release = ReleaseReading(
        event_key="us_cpi_mom",
        event_name="CPI m/m",
        headline="US CPI m/m 0.1% vs 0.3% expected",
        actual=0.1,
        forecast=0.3,
        previous=0.2,
        unit="%",
        surprise=-0.2,
        z=-2.0,
        hawkish_z=-2.0,
        z_basis="prior",
        bucket="strong_bullish",
        direction_gold="bullish",
        first_seen_at=T0 + timedelta(seconds=2),
        parsed_at=T0 + timedelta(seconds=2),
    )
    text = render_flash(
        release, now=T0 + timedelta(seconds=6), confirm_seconds=60, news_trading=False
    )
    assert text.split("\n") == [
        "🔴 <b>FLASH</b> | CPI m/m 0.1% (forecast 0.3%, sebelumnya 0.2%)",
        "Surprise -0.2% (z=-2.0) → bullish emas · skenario playbook: bullish kuat",
        "Menunggu konfirmasi harga 60 dtk sebelum sinyal…",
        "Diterima 19:30:02 WIB · latensi 4.0 dtk",
        "<i>z-score memakai simpangan baku awal (belum terkalibrasi)</i>",
    ]


@pytest.mark.parametrize(
    ("key", "title", "forecast", "first", "last"),
    [
        ("us_cpi_mom", "CPI m/m", "0.3%", "• ≥ 0.45% → bearish kuat", "• ≤ 0.15% → bullish kuat"),
        # Higher unemployment is dovish: the top of the table is the bullish side.
        (
            "us_unemployment_rate",
            "Unemployment Rate",
            "4.3%",
            "• ≥ 4.45% → bullish kuat",
            "• ≤ 4.15% → bearish kuat",
        ),
    ],
)
def test_playbook_lists_buckets_from_highest_actual_down(
    key: str, title: str, forecast: str, first: str, last: str
) -> None:
    spec = load_events().by_key(key)
    assert spec is not None
    release = event(title, at=T0, key=key, forecast=forecast)
    playbook = build_playbook(release, spec, surprise_std=0.1, z_basis="prior", now=T0)
    lines = render_playbook(playbook, now=T0 - timedelta(minutes=30)).split("\n")
    assert lines[0] == f"📋 <b>PLAYBOOK</b> · USD {title} · 19:30 WIB (30 mnt lagi)"
    buckets = [line for line in lines if line.startswith("• ")]
    assert len(buckets) == 5
    assert buckets[0].startswith(first) and buckets[-1].startswith(last)


def test_outlook_message_fits_telegram_after_splitting(settings: Settings) -> None:
    builder = OutlookBuilder(settings, SessionClock(settings.sessions), load_events())
    outlook = builder.build(
        OutlookInputs(
            now=T0,
            trigger="pembukaan sesi New York",
            context=market(levels=LEVELS),
            state=None,
            active=None,
            events=[event(at=T0)],
        )
    )
    text = render_outlook(outlook)
    assert "TUNGGU DATA" in text and "↳ CPI m/m 19:30:" in text
    chunks = split_message(text)
    assert all(len(chunk) <= TELEGRAM_LIMIT for chunk in chunks)
    assert "\n".join(chunks) == text


def test_split_keeps_lines_whole_and_cuts_only_monster_lines() -> None:
    text = "\n".join(f"<b>baris {n}</b> " + "x" * 90 for n in range(100))
    chunks = split_message(text)
    assert len(chunks) > 1 and all(len(c) <= TELEGRAM_LIMIT for c in chunks)
    assert all(c.count("<b>") == c.count("</b>") for c in chunks)
    monster = split_message("<b>" + "&" * 5000 + "</b>")
    assert len(monster) == 1 and len(monster[0]) <= TELEGRAM_LIMIT
    assert "<b>" not in monster[0] and strip_tags(monster[0]).startswith("&&&")

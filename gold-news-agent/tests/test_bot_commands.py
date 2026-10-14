"""Bot commands (allowlist, kill switch, mute, read-only views) and the Telegram sender."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from telegram.error import BadRequest, RetryAfter, TimedOut

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_EVENTS
from gold_agent.core.clock import ManualClock
from gold_agent.models import Level, SignalOutcome
from gold_agent.notify import telegram as telegram_module
from gold_agent.notify.commands import BotCommands, parse_duration
from gold_agent.notify.dispatcher import Dispatcher
from gold_agent.notify.telegram import TelegramSender, parse_admin_ids
from gold_agent.signals.engine import KV_ENGINE, KV_HALT, EngineState, SignalEngine
from gold_agent.timeutils.sessions import SessionClock
from tests.builders import T0, analysis, event, flat_prices, market

ADMIN = 1111
CHAT = "-100200"


@pytest.fixture
async def commands(ctx: AppContext, clock: ManualClock, settings: Settings) -> BotCommands:
    clock.set(T0)
    dispatcher = Dispatcher(settings, repo=ctx.notify, ops=ctx.ops, clock=clock, sender=None)
    await dispatcher.load()
    return BotCommands(ctx, dispatcher, admin_ids=frozenset({ADMIN}), chat_id=CHAT)


def test_admin_ids_and_durations_parse_leniently() -> None:
    assert parse_admin_ids(" 1111, 2222;abc, -5 ") == frozenset({1111, 2222, -5})
    assert parse_admin_ids(None) == frozenset()
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("2jam") == timedelta(hours=2)
    assert parse_duration("1h") == timedelta(hours=1) and parse_duration("lama") is None


def test_only_admins_in_the_configured_or_private_chat_are_obeyed(commands: BotCommands) -> None:
    assert commands.authorized(ADMIN, int(CHAT))
    assert commands.authorized(ADMIN, ADMIN)  # the admin's private chat with the bot
    assert not commands.authorized(2222, int(CHAT))  # not an admin
    assert not commands.authorized(ADMIN, -100999)  # an admin, but some other group
    assert not commands.authorized(None, int(CHAT))


async def test_stop_and_resume_flip_the_halt_flag_without_any_other_service(
    commands: BotCommands, ctx: AppContext
) -> None:
    reply = await commands.run("stop", [], user_id=ADMIN)
    assert reply.startswith("🛑 Kill switch aktif")
    halt = await ctx.ops.kv_get(KV_HALT)
    assert halt is not None and halt["halted"] is True and halt["by"] == ADMIN
    await commands.run("resume", [], user_id=ADMIN)
    halt = await ctx.ops.kv_get(KV_HALT)
    assert halt is not None and halt["halted"] is False
    events = await ctx.bus.read(STREAM_EVENTS, "t", "t", count=10, block_ms=0)
    assert [(e.data["type"], e.data["halted"]) for e in events] == [
        ("signal.halt", True),
        ("signal.halt", False),
    ]


async def test_mute_command(commands: BotCommands, clock: ManualClock) -> None:
    reply = await commands.run("mute", ["30m"], user_id=ADMIN)
    assert reply.startswith("🔕 Notifikasi 🟡/🔵 dibisukan s/d Selasa 13 Okt 20:00 WIB")
    assert await commands.run("mute", ["kapan-kapan"], user_id=ADMIN) == (
        "Format: /mute 30m, /mute 1h, /mute 2jam, atau /mute off"
    )
    long = await commands.run("mute", ["99h"], user_id=ADMIN)
    assert "Rabu 14 Okt 19:30" in long  # capped at 24 hours
    assert await commands.run("mute", ["off"], user_id=ADMIN) == "🔔 Notifikasi aktif kembali."


async def test_views_say_plainly_when_there_is_no_data(commands: BotCommands) -> None:
    status = await commands.run("status", [], user_id=ADMIN)
    assert "💵 Harga: data tidak tersedia" in status
    assert "📊 Status sinyal belum tersedia" in status
    assert await commands.run("signal", [], user_id=ADMIN) == (
        "Belum ada sinyal. Status saat ini: /status"
    )
    assert await commands.run("outlook", [], user_id=ADMIN) == "Outlook belum tersedia."
    assert "Tidak ada event" in await commands.run("calendar", [], user_id=ADMIN)
    stats = await commands.run("stats", [], user_id=ADMIN)
    assert "sampel belum cukup (0 sinyal tertutup, butuh ≥ 30)" in stats


async def test_views_show_the_live_state(
    commands: BotCommands, ctx: AppContext, settings: Settings
) -> None:
    engine = SignalEngine(settings, SessionClock(settings.sessions), config_version="x")
    decision = engine.evaluate(
        now=T0,
        context=market(
            dxy_z_1h=-1.5,
            dxy_z_15m=-1.0,
            levels=[Level(name="swing low H1", price=2644.0, kind="support")],
        ),
        analyses=[analysis(first_seen=T0 - timedelta(minutes=5))],
        events=[],
        state=EngineState(),
        price_at=flat_prices(2645.0),
    )
    signal = decision.signal
    assert signal is not None
    state = EngineState()
    state.record_signal(signal)
    await ctx.ops.kv_set(KV_ENGINE, state.to_json(), T0)
    await ctx.signals.save_signal(signal)
    await ctx.signals.save_state(decision.state)
    await ctx.market.save_context(market())
    await ctx.calendar.upsert_events([event(at=T0 + timedelta(hours=2))], T0)
    await ctx.signals.save_outcome(
        SignalOutcome(signal_id=signal.signal_id, status="open", entry_price=2650.1, opened_at=T0)
    )

    status = await commands.run("status", [], user_id=ADMIN)
    assert "💵 2650.00 (mt5) · spread 0.20 · tick 1 dtk" in status
    assert "📊 BUY · skor +" in status and "🎯 Sinyal aktif: BUY 2649.60–2650.40" in status
    assert "USD CPI m/m Selasa 13 Okt 21:30 WIB (2 jam lagi)" in status
    assert (await commands.run("signal", [], user_id=ADMIN)).startswith("🟢 <b>BUY XAUUSD</b>")
    calendar = await commands.run("calendar", [], user_id=ADMIN)
    assert "🔴 Selasa 13 Okt 21:30 USD CPI m/m (F 0.3%, P 0.2%) · 2 jam lagi" in calendar
    assert "terbuka 1" in await commands.run("stats", [], user_id=ADMIN)


# ------------------------------------------------------------------ sender


class FakeBot:
    def __init__(self, *script: BaseException | None) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(telegram_module.asyncio, "sleep", fake_sleep)
    return waits


@pytest.mark.filterwarnings("ignore::telegram.warnings.PTBDeprecationWarning")  # PTB's own ctor
async def test_sender_waits_out_flood_control_and_retries_timeouts(no_sleep: list[float]) -> None:
    bot = FakeBot(RetryAfter(7), TimedOut(), None)
    await TelegramSender(bot, CHAT).send("<b>halo</b>")
    assert len(bot.calls) == 3 and bot.calls[-1]["parse_mode"] == "HTML"
    assert no_sleep == [7.5, 4.0]


async def test_sender_falls_back_to_plain_text_when_html_is_rejected(no_sleep: list[float]) -> None:
    bot = FakeBot(BadRequest("Can't parse entities: unclosed tag"), None)
    await TelegramSender(bot, CHAT).send("<b>harga & spread")
    assert bot.calls[-1]["parse_mode"] is None and bot.calls[-1]["text"] == "harga & spread"


async def test_sender_gives_up_after_repeated_network_errors(no_sleep: list[float]) -> None:
    bot = FakeBot(TimedOut(), TimedOut(), TimedOut(), TimedOut())
    with pytest.raises(TimedOut):
        await TelegramSender(bot, CHAT).send("x")
    assert len(bot.calls) == 4

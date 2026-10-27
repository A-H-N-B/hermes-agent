"""Bot commands, independent of Telegram types so they are testable and reusable.

/stop must keep working when the LLM, the signal service or the market feed are down: it writes
the halt flag straight to kv_state and also tells the signal service over the bus.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from gold_agent.analysis.llm_client import Turn
from gold_agent.assistant.chat import MAX_TURNS, ChatAssistant, ChatFacts
from gold_agent.core.app import AppContext
from gold_agent.core.bus import STREAM_EVENTS
from gold_agent.evaluation.performance import summarize
from gold_agent.market.rebase import request_calibration
from gold_agent.models import CalendarEvent, Signal
from gold_agent.notify import templates
from gold_agent.notify.dispatcher import Dispatcher
from gold_agent.notify.format import esc
from gold_agent.signals.desk import model_name
from gold_agent.signals.engine import KV_ENGINE, KV_HALT, EngineState
from gold_agent.timeutils.sessions import fmt_wib

_DURATION = re.compile(r"^(\d+)\s*(m|mnt|menit|h|j|jam)?$", re.IGNORECASE)
MAX_MUTE = timedelta(hours=24)

HELP = "\n".join(
    [
        "🤖 <b>Perintah</b>",
        "/tanya kapan aku harus buy? — tanya AI (atau ketik pesan biasa tanpa /)",
        "/status — harga, sesi, status sinyal, event berikutnya",
        "/signal — sinyal aktif / terakhir",
        "/outlook — outlook 24 jam terbaru",
        "/calendar — event 48 jam ke depan",
        "/stats — performa paper trading",
        "/mute 1h — bisukan notifikasi 🟡/🔵 (🔴 tetap masuk); /mute off",
        "/finex 4192.23 — samakan harga Binance dengan Bid brokermu; /finex reset",
        "/stop — kill switch: hentikan sinyal baru; /resume — lanjutkan",
    ]
)


def parse_duration(text: str) -> timedelta | None:
    match = _DURATION.match(text.strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = (match.group(2) or "h").lower()
    return timedelta(minutes=amount) if unit.startswith("m") else timedelta(hours=amount)


class BotCommands:
    names = (
        "start",
        "help",
        "tanya",
        "status",
        "signal",
        "outlook",
        "calendar",
        "stats",
        "mute",
        "finex",
        "stop",
        "resume",
    )

    def __init__(
        self,
        ctx: AppContext,
        dispatcher: Dispatcher,
        *,
        admin_ids: frozenset[int],
        chat_id: str | None,
        assistant: ChatAssistant | None = None,
        facts: Callable[[], Awaitable[ChatFacts]] | None = None,
    ) -> None:
        """`assistant` + `facts`: the chat (assistant/chat.py) and what it answers from."""
        self._ctx = ctx
        self._dispatcher = dispatcher
        self._admins = admin_ids
        self._chat_id = chat_id
        self._assistant = assistant
        self._facts = facts
        self._history: dict[int | None, list[Turn]] = {}  # per user, this process only
        self._table: dict[str, Callable[[list[str], int | None], Awaitable[str]]] = {
            "start": self._help,
            "help": self._help,
            "tanya": self._tanya,
            "status": self._status,
            "signal": self._signal,
            "outlook": self._outlook,
            "calendar": self._calendar,
            "stats": self._stats,
            "mute": self._mute,
            "finex": self._finex,
            "stop": self._stop,
            "resume": self._resume,
        }

    def authorized(self, user_id: int | None, chat_id: int | None) -> bool:
        if user_id is None or user_id not in self._admins or chat_id is None:
            return False
        return str(chat_id) == str(self._chat_id) or chat_id == user_id

    async def run(self, name: str, args: list[str], *, user_id: int | None) -> str:
        return await self._table[name](args, user_id)

    # ------------------------------------------------------------------ shared reads

    async def _engine_state(self) -> EngineState:
        stored = await self._ctx.ops.kv_get(KV_ENGINE)
        return EngineState.from_json(stored) if stored else EngineState()

    async def _next_event(self, now: datetime) -> CalendarEvent | None:
        events = await self._ctx.calendar.between(now, now + timedelta(days=7))
        return next((e for e in events if e.impact == "high" and not e.all_day), None)

    # ------------------------------------------------------------------ commands

    async def _help(self, _args: list[str], _user: int | None) -> str:
        return HELP

    async def _status(self, _args: list[str], _user: int | None) -> str:
        ctx = self._ctx
        now = ctx.clock.now()
        state = await ctx.signals.latest_state()
        halt = await ctx.ops.kv_get(KV_HALT)
        if state is not None and halt is not None:
            state = state.model_copy(update={"halted": bool(halt.get("halted"))})
        return templates.render_status(
            now=now,
            state=state,
            context=await ctx.market.latest_context(),
            active=(await self._engine_state()).active(now),
            next_event=await self._next_event(now),
            muted_until=self._dispatcher.mute_until,
            mode=ctx.settings.mode,
        )

    async def _signal(self, _args: list[str], _user: int | None) -> str:
        ctx = self._ctx
        now = ctx.clock.now()
        engine = await self._engine_state()
        active = engine.active(now)
        if active is not None:
            return templates.render_signal(active, now=now, next_event=await self._next_event(now))
        latest: list[Signal] = await ctx.signals.latest_signals(limit=1)
        if not latest:
            return "Belum ada sinyal. Status saat ini: /status"
        last = latest[0]
        return (
            f"Tidak ada sinyal aktif. Terakhir: {last.action} {esc(last.created_at_wib)} "
            f"(entry {last.entry_low:.2f}–{last.entry_high:.2f}, berakhir "
            f"{esc(last.valid_until_wib)})."
        )

    async def _outlook(self, _args: list[str], _user: int | None) -> str:
        outlook = await self._ctx.signals.latest_outlook()
        if outlook is None:
            return "Outlook belum tersedia."
        return templates.render_outlook(outlook)

    async def _calendar(self, _args: list[str], _user: int | None) -> str:
        now = self._ctx.clock.now()
        events = await self._ctx.calendar.between(now - timedelta(hours=1), now + timedelta(days=2))
        shown = [e for e in events if e.impact in ("high", "medium")][:25]
        return templates.render_calendar(shown, now=now)

    async def _stats(self, _args: list[str], _user: int | None) -> str:
        ctx = self._ctx
        now = ctx.clock.now()
        since = now - timedelta(days=90)
        signals = await ctx.signals.signals_between(since, now + timedelta(seconds=1))
        outcomes = await ctx.signals.outcomes_between(since, now + timedelta(seconds=1))
        return templates.render_stats(summarize(signals, outcomes))

    async def _mute(self, args: list[str], _user: int | None) -> str:
        word = args[0].lower() if args else "1h"
        if word in ("off", "0", "batal"):
            await self._dispatcher.mute(None)
            return "🔔 Notifikasi aktif kembali."
        duration = parse_duration(word)
        if duration is None or duration <= timedelta(0):
            return "Format: /mute 30m, /mute 1h, /mute 2jam, atau /mute off"
        until = await self._dispatcher.mute(min(duration, MAX_MUTE))
        when = fmt_wib(until, with_date=True) if until else "-"
        return (
            f"🔕 Notifikasi 🟡/🔵 dibisukan s/d {when} WIB. "
            "Sinyal & berita 🔴 tetap masuk; sisanya dikirim sebagai ringkasan."
        )

    @property
    def can_chat(self) -> bool:
        return self._assistant is not None and self._facts is not None

    async def ask(self, question: str, user_id: int | None) -> str:
        """A question in plain words (a /tanya or any non-command message from an admin)."""
        if self._assistant is None or self._facts is None:
            return "Fitur tanya AI belum aktif di layanan ini."
        if not question.strip():
            return "Tulis pertanyaannya, misalnya: /tanya kapan aku harus buy?"
        history = self._history.setdefault(user_id, [])
        reply = await self._assistant.answer(question, history, await self._facts())
        history += [("user", question), ("assistant", reply.text)]
        del history[: max(0, len(history) - MAX_TURNS)]
        by = f"🤖 {model_name(reply.model)}" if reply.model else f"⚙️ tanpa AI: {reply.note}"
        return f"{esc(reply.text)}\n\n<i>{esc(by)}</i>"

    async def _tanya(self, args: list[str], user_id: int | None) -> str:
        return await self.ask(" ".join(args), user_id)

    async def _finex(self, args: list[str], user_id: int | None) -> str:
        word = args[0].replace(",", ".") if args else ""
        if word.lower() in ("reset", "asli", "off"):
            broker = None
        else:
            try:
                broker = float(word)
            except ValueError:
                return "Format: /finex 4192.23 (harga Bid XAUUSD di brokermu), atau /finex reset"
            if not 0 < broker < 1e6:
                return "Harga tidak wajar. Contoh: /finex 4192.23"
        result = await request_calibration(self._ctx, broker, by=f"telegram:{user_id}")
        return f"💱 {esc(result)}"

    async def _set_halt(self, halted: bool, user_id: int | None) -> None:
        ctx = self._ctx
        now = ctx.clock.now()
        payload = {"halted": halted, "by": user_id, "at": now.isoformat()}
        await ctx.ops.kv_set(KV_HALT, payload, now)  # survives even if the bus is down
        await ctx.bus.publish(STREAM_EVENTS, {"type": "signal.halt", **payload})

    async def _stop(self, _args: list[str], user_id: int | None) -> str:
        await self._set_halt(True, user_id)
        return (
            "🛑 Kill switch aktif: tidak ada sinyal baru sampai /resume. "
            "Sinyal yang sudah terkirim tetap dipantau paper tracker."
        )

    async def _resume(self, _args: list[str], user_id: int | None) -> str:
        await self._set_halt(False, user_id)
        return "▶️ Sinyal dilanjutkan."

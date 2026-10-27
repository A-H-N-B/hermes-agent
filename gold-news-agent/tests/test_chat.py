"""Tanya AI (assistant/chat.py): questions answered from what the agent knows now, by Opus 5.5
with every fact written by the program, or by the program's own summary when there is no AI;
on the dashboard and on Telegram."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest

from gold_agent.analysis.llm_client import LlmClient, Turn
from gold_agent.assistant.chat import ChatAssistant, ChatFacts, chat_brief, gather_facts
from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.dashboard.app import create_app
from gold_agent.dashboard.live import LiveHub
from gold_agent.models import Impulse, SignalComponents, SignalState
from gold_agent.notify.commands import BotCommands
from gold_agent.notify.dispatcher import Dispatcher
from tests.builders import T0, event, market
from tests.dashboard_seed import NOW, seed_dashboard
from tests.fakes import FakeAnthropic, written

ANSWER = "Sekarang TUNGGU. BUY bila harga tembus 2668.00. Bukan saran keuangan."


def waiting(score: float = 0.12) -> SignalState:
    return SignalState(
        ts=NOW,
        action="WAIT",
        composite_score=score,
        confidence=30,
        components=SignalComponents(N=0.1, M=-0.05, T=0.3, P=0.1, session_factor=1.0),
        wait_reasons=["skor gabungan +0.12 di bawah ambang ±0.35"],
        reasons=["Teknikal: tren H1 naik"],
    )


def facts(**extra: Any) -> ChatFacts:
    impulse = Impulse(
        side="BUY",
        z=4.0,
        window=3,
        move=6.0,
        level=2649.5,
        trigger=2649.8,
        range_minutes=30,
        since=NOW,
    )
    context = market(ts=NOW, price_offset=12.4, proxy=True, impulse=impulse)
    base: dict[str, Any] = {
        "now": NOW,
        "context": context,
        "state": waiting(),
        "active": None,
        "outlook": None,
        "events": [event("CPI m/m", at=NOW + timedelta(hours=2))],
        "analyses": [],
        "fundamentals": None,
        "choice": None,
    }
    return ChatFacts(**(base | extra))


def test_the_brief_carries_the_facts_an_answer_needs(settings: Settings) -> None:
    brief = chat_brief(facts(), settings)
    assert "sudah disamakan ke harga broker (selisih +12.40)" in brief
    assert '<mesin aksi="WAIT" skor="+0.12" ambang="±0.35"' in brief
    assert "menunggu karena: skor gabungan +0.12 di bawah ambang ±0.35" in brief
    assert "<sinyal_aktif>tidak ada</sinyal_aktif>" in brief
    assert "impuls harga naik kuat: +6.00$ dalam 3 mnt" in brief
    assert "- jangan trading 21:20–21:36 WIB: rilis USD CPI m/m" in brief  # the blackout
    assert "USD CPI m/m [high]" in brief


def test_without_data_the_brief_says_so_instead_of_guessing(settings: Settings) -> None:
    brief = chat_brief(facts(context=None, state=None, events=[]), settings)
    assert "<harga>data harga tidak tersedia</harga>" in brief
    assert "pasar -" in brief and "pasar tutup" not in brief  # unknown, not closed
    assert "<mesin>status mesin belum tersedia</mesin>" in brief


def assistant(settings: Settings, ctx: AppContext, fake: Any) -> ChatAssistant:
    llm = LlmClient(settings, api_key=None, ops=ctx.ops, clock=ctx.clock, client=fake)
    return ChatAssistant(settings, llm, ctx.ops)


async def test_opus_answers_from_the_brief_with_the_conversation_so_far(
    settings: Settings, ctx: AppContext, clock: ManualClock
) -> None:
    await ctx.db.create_all()
    clock.set(NOW)
    fake = FakeAnthropic(written(ANSWER))
    history: list[Turn] = [("user", "halo"), ("assistant", "Halo, ada yang bisa dibantu?")]
    reply = await assistant(settings, ctx, fake).answer("kapan aku harus buy?", history, facts())
    assert (reply.text, reply.model) == (ANSWER, "claude-opus-5-5")
    (request,) = fake.requests
    assert request["output_config"] == {"effort": "low"}
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    roles = [m["role"] for m in request["messages"]]
    assert roles == ["user", "assistant", "user"]  # the earlier turns, then facts + question
    last = request["messages"][-1]["content"]
    assert last.startswith("<waktu>") and last.endswith(
        "<pertanyaan>kapan aku harus buy?</pertanyaan>"
    )


async def test_markdown_in_an_answer_is_shown_as_plain_words(
    settings: Settings, ctx: AppContext, clock: ManualClock
) -> None:
    await ctx.db.create_all()
    clock.set(NOW)
    fake = FakeAnthropic(written("## Jawaban\n**Sekarang:** TUNGGU.\n* BUY bila > 2668*1"))
    reply = await assistant(settings, ctx, fake).answer("buy?", [], facts())
    assert reply.text == "Jawaban\nSekarang: TUNGGU.\n- BUY bila > 2668*1"


async def test_without_the_ai_the_program_still_answers(
    settings: Settings, ctx: AppContext, clock: ManualClock
) -> None:
    await ctx.db.create_all()
    clock.set(NOW)
    disabled = settings.model_copy(
        update={"llm": settings.llm.model_copy(update={"enabled": False})}
    )
    reply = await assistant(disabled, ctx, FakeAnthropic()).answer("buy?", [], facts())
    assert reply.model is None and reply.note is not None
    assert reply.text.startswith("Sekarang TUNGGU (skor +0.12): skor gabungan")
    capped = settings.model_copy(
        update={"llm": settings.llm.model_copy(update={"chat_per_day": 1})}
    )
    first = assistant(capped, ctx, FakeAnthropic(written(ANSWER)))
    assert (await first.answer("buy?", [], facts())).model == "claude-opus-5-5"
    # Another surface (Telegram after the dashboard) or a restart counts the same answers.
    other = assistant(capped, ctx, FakeAnthropic(written(ANSWER)))
    second = await other.answer("lagi?", [], facts())
    assert second.model is None and second.note == "batas 1 pertanyaan AI per hari tercapai"


# ------------------------------------------------------------------ dashboard and Telegram


@pytest.fixture
async def http(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    clock.set(NOW)
    await seed_dashboard(ctx, settings)
    app = create_app(ctx, LiveHub(), password=None, anthropic_client=FakeAnthropic(written(ANSWER)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_the_dashboard_chat_answers_same_origin_json_only(http: httpx.AsyncClient) -> None:
    payload = {"message": "kapan aku harus buy?", "history": [{"role": "user", "text": "hai"}]}
    reply = await http.post("/api/chat", json=payload)
    assert reply.status_code == 200 and reply.json()["answer"] == ANSWER
    assert (await http.post("/api/chat", json={"message": "  "})).status_code == 422
    foreign = await http.post("/api/chat", json=payload, headers={"origin": "https://evil.example"})
    assert foreign.status_code == 403


async def test_telegram_questions_get_an_escaped_answer_and_keep_the_thread(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    await ctx.db.create_all()
    clock.set(T0)
    fake = FakeAnthropic(written("BUY bila > 2668 & RSI < 70"), written(ANSWER))
    dispatcher = Dispatcher(settings, repo=ctx.notify, ops=ctx.ops, clock=clock, sender=None)

    async def live() -> ChatFacts:
        return await gather_facts(ctx, context=market(ts=T0), state=waiting())

    commands = BotCommands(
        ctx,
        dispatcher,
        admin_ids=frozenset({1}),
        chat_id="1",
        assistant=assistant(settings, ctx, fake),
        facts=live,
    )
    first = await commands.run("tanya", ["kapan", "buy?"], user_id=1)
    assert first == "BUY bila &gt; 2668 &amp; RSI &lt; 70\n\n<i>🤖 Opus 5.5</i>"
    await commands.ask("lalu SL di mana?", 1)  # a plain message continues the same thread
    roles = [m["role"] for m in fake.requests[1]["messages"]]
    assert roles == ["user", "assistant", "user"]

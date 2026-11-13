"""How far gold moved in percent (dashboard/moves.py): today from the trading day's open, each
recent day from open to close, and against the price N days ago, which is the last close known
then (a weekend gives Friday's close). The market context carries the day's open, high and low
from the live daily bar, and the dashboard serves it all at /api/moves."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from gold_agent.assistant.chat import chat_brief, gather_facts
from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.dashboard.app import create_app
from gold_agent.dashboard.live import LiveHub
from gold_agent.dashboard.moves import move_line, price_moves
from gold_agent.models import Candle, MarketContext
from gold_agent.notify.commands import BotCommands
from gold_agent.notify.dispatcher import Dispatcher
from gold_agent.timeutils.sessions import SessionClock
from tests.test_rebase import AT, service_with_history, tick

MONDAY = datetime(2026, 10, 12, 4, 0, tzinfo=UTC)  # Mon 11:00 WIB, inside Monday's trading day


def bar(
    tf: str, opened: datetime, minutes: int, close: float, open_: float | None = None
) -> Candle:
    start = close if open_ is None else open_
    return Candle(
        source="mt5",
        tf=tf,  # type: ignore[arg-type]
        open_time=opened,
        close_time=opened + timedelta(minutes=minutes),
        open=start,
        high=max(start, close) + 1,
        low=min(start, close) - 1,
        close=close,
        complete=True,
    )


def context(**extra: object) -> MarketContext:
    base: dict[str, object] = {
        "ts": MONDAY,
        "source": "mt5",
        "bid": 4019.9,
        "ask": 4020.1,
        "mid": 4020.0,
        "market_open": True,
        "day_open": 4000.0,
        "day_open_at": datetime(2026, 10, 11, 21, 0, tzinfo=UTC),  # Sun 17:00 New York
        "day_high": 4040.0,
        "day_low": 3990.0,
    }
    return MarketContext.model_validate(base | extra)


def m15_until_friday_close() -> list[Candle]:
    """Quarter-hour bars from Wednesday to Friday 17:00 New York, the price rising $1 a bar;
    then the weekend (no bars) and Monday's bars from the Sunday open."""
    bars: list[Candle] = []
    start, friday_close = (
        datetime(2026, 10, 7, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 9, 21, 0, tzinfo=UTC),
    )
    price, at = 3900.0, start
    while at < friday_close:
        bars.append(bar("M15", at, 15, price))
        price, at = price + 1.0, at + timedelta(minutes=15)
    at = datetime(2026, 10, 11, 21, 0, tzinfo=UTC)
    while at + timedelta(minutes=15) <= MONDAY:
        bars.append(bar("M15", at, 15, 4000.0))
        at += timedelta(minutes=15)
    return bars


def test_today_is_measured_from_the_open_with_the_days_range_in_percent() -> None:
    moves = price_moves(context=context(), daily=[], fine=[], coarse=[], now=MONDAY)
    today = moves["today"]
    assert (today["pct"], today["high_pct"], today["low_pct"]) == (0.5, 1.0, -0.25)
    assert today["open_at_wib"].endswith("04:00")  # Sunday 17:00 New York = Monday 04:00 WIB
    # The price now beyond the stored high widens the range at once.
    higher = price_moves(context=context(mid=4050.0), daily=[], fine=[], coarse=[], now=MONDAY)
    assert higher["today"]["high_pct"] == 1.25 and higher["today"]["pct"] == 1.25


def test_days_back_compare_with_the_last_close_known_then_across_a_weekend() -> None:
    fine = m15_until_friday_close()
    moves = price_moves(context=context(), daily=[], fine=fine, coarse=[], now=MONDAY)
    by_days = {row["days"]: row for row in moves["lookbacks"]}
    friday_last = fine[[b.close_time for b in fine].index(datetime(2026, 10, 9, 21, 0, tzinfo=UTC))]
    one = by_days[1]  # Sunday 04:00 UTC: the market was shut, so Friday's last close
    assert one["price"] == friday_last.close and one["at_wib"].startswith("Sabtu 10 Okt 04:00")
    assert one["pct"] == pytest.approx((4020.0 / friday_last.close - 1) * 100, abs=1e-3)
    three = by_days[3]  # Friday 04:00 UTC: inside Friday's trading
    assert three["price"] == next(
        b.close for b in fine if b.close_time == MONDAY - timedelta(days=3)
    )
    assert by_days[6] == {"days": 6, "price": None, "note": "data belum cukup"}  # before Wednesday
    assert [row["days"] for row in moves["lookbacks"]] == [1, 2, 3, 4, 5, 6, 7]


def test_longer_look_backs_read_hourly_bars_and_each_day_runs_open_to_close() -> None:
    coarse = [bar("H1", MONDAY - timedelta(days=30, hours=1), 60, 3800.0)]
    daily = [
        bar("D1", datetime(2026, 10, 7, 21, 0, tzinfo=UTC), 1440, 3950.0, open_=3900.0),
        bar("D1", datetime(2026, 10, 8, 21, 0, tzinfo=UTC), 1440, 3910.5, open_=3950.0),
        bar("D1", datetime(2026, 10, 11, 21, 0, tzinfo=UTC), 1440, 4020.0, open_=4000.0),
    ]
    moves = price_moves(
        context=context(), daily=daily, fine=[], coarse=coarse, now=MONDAY, extra_days=30
    )
    thirty = next(row for row in moves["lookbacks"] if row["days"] == 30)
    assert thirty["price"] == 3800.0 and thirty["pct"] == pytest.approx(5.789, abs=1e-3)
    # Today's bar is shown live from the context, not again from the stored daily bars.
    assert [(row["label"], row["pct"]) for row in moves["days"]] == [
        ("Kamis 8 Okt", pytest.approx(1.282, abs=1e-3)),
        ("Jumat 9 Okt", pytest.approx(-1.0, abs=1e-3)),
    ]
    line = move_line(moves)
    assert line is not None and line.startswith("hari ini +0.50% sejak buka Senin 12 Okt 04:00")


async def test_the_context_carries_the_trading_days_open_high_and_low(
    ctx_factory: Callable[..., AppContext], clock: ManualClock, settings: Settings
) -> None:
    service, ctx = await service_with_history(ctx_factory, clock)
    try:
        snapshot = await service.publish_context()
        trading_day = SessionClock(settings.sessions).trading_day_start(AT)
        assert snapshot.day_open_at == trading_day  # 17:00 New York the day before
        assert snapshot.day_open is not None and snapshot.day_high is not None
        clock.advance(1)
        await service.on_tick(tick(snapshot.day_high + 25.0, clock.now()))
        higher = await service.publish_context()
        assert higher.day_high == pytest.approx(snapshot.day_high + 25.2)  # the new tick's mid
        assert higher.day_open == snapshot.day_open
    finally:
        await ctx.close()


@pytest.fixture
async def http(ctx: AppContext, clock: ManualClock) -> AsyncIterator[httpx.AsyncClient]:
    clock.set(MONDAY)
    await ctx.market.upsert_candles(m15_until_friday_close())
    hub = LiveHub()
    await hub.on_context(context().model_dump(mode="json"))  # the newest pushed context
    app = create_app(ctx, hub, password=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_the_dashboard_serves_the_moves_and_any_number_of_days(
    http: httpx.AsyncClient,
) -> None:
    moves = (await http.get("/api/moves")).json()
    assert moves["today"]["pct"] == 0.5 and moves["price"] == 4020.0
    assert {row["days"] for row in moves["lookbacks"]} == {1, 2, 3, 4, 5, 6, 7}
    with_ten = (await http.get("/api/moves", params={"days": 10})).json()
    assert {row["days"] for row in with_ten["lookbacks"]} == {1, 2, 3, 4, 5, 6, 7, 10}
    assert (await http.get("/api/moves", params={"days": 61})).status_code == 422


async def test_telegram_status_and_the_chat_say_how_far_gold_moved(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    clock.set(MONDAY)
    await ctx.market.upsert_candles(m15_until_friday_close())
    await ctx.market.save_context(context())
    dispatcher = Dispatcher(settings, repo=ctx.notify, ops=ctx.ops, clock=clock, sender=None)
    commands = BotCommands(ctx, dispatcher, admin_ids=frozenset({1}), chat_id="1")
    status = await commands.run("status", [], user_id=1)
    assert "📈 Naik/turun: hari ini +0.50% sejak buka Senin 12 Okt 04:00 WIB" in status
    facts = await gather_facts(ctx, context=None, state=None)
    brief = chat_brief(facts, settings)
    assert "<pergerakan>hari ini +0.50% sejak buka" in brief and "1 hari -3.71%" in brief

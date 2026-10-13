"""Behaviour of the Phase 0 source verifier.

Inputs here are minimal structural documents that exercise parser and timing logic. Real feed
bodies are recorded with `verify_sources.py --record` once sources are reachable and become the
regression fixtures for the Phase 1 collectors.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from gold_agent.config import Source
from gold_agent.ops.source_check import (
    FormatError,
    Redactor,
    RobotsCache,
    check_http_source,
    check_telegram_source,
    check_websocket_source,
    lag_stats,
    parse_ics,
    parse_json_items,
    parse_rss,
    run_checks,
    timestamp_precision,
    watch_lag,
)

T0 = datetime(2026, 10, 13, 12, 30, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def rss(*items: tuple[str, datetime]) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><guid>{title}</guid>"
        f"<pubDate>{format_datetime(published)}</pubDate></item>"
        for title, published in items
    )
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'
    ).encode()


def source(**overrides: Any) -> Source:
    base: dict[str, Any] = {
        "id": "feed",
        "name": "Feed",
        "kind": "news",
        "type": "rss",
        "url": "https://news.example.com/feed",
        "tier": 1,
        "poll_seconds": 10,
        "reliability": 0.8,
        "enabled": True,
        "status": "candidate",
        "tos_note": "test",
    }
    return Source.model_validate(base | overrides)


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


# --------------------------------------------------------------------------- pure helpers


def test_redactor_hides_secret_values_and_secret_query_params() -> None:
    redact = Redactor(["s3cr3t-value", "abc"])
    text = "https://x.io/?apikey=s3cr3t-value&token=zzz&q=gold failed for s3cr3t-value"
    cleaned = redact(text)
    assert "s3cr3t-value" not in cleaned
    assert "zzz" not in cleaned
    assert "q=gold" in cleaned
    assert "abc" in redact("abc")  # too short to be treated as a secret value


def test_timestamp_precision_reports_the_coarsest_consistent_unit() -> None:
    assert timestamp_precision([]) == "none"
    assert timestamp_precision([T0.replace(hour=0, minute=0)]) == "date"
    assert timestamp_precision([T0, T0.replace(minute=31)]) == "minute"
    assert timestamp_precision([T0, T0.replace(second=7)]) == "second"


def test_lag_stats_percentiles_and_negative_count() -> None:
    stats = lag_stats([1.0, 2.0, 3.0, 4.0, -1.0])
    assert stats.samples == 5
    assert stats.p50_seconds == 2.0
    assert stats.max_seconds == 4.0
    assert stats.negative == 1
    assert lag_stats([]).p50_seconds is None


# --------------------------------------------------------------------------- parsers


def test_parse_rss_normalises_published_times_to_utc() -> None:
    jakarta = datetime(2026, 10, 13, 19, 30, tzinfo=ZoneInfo("Asia/Jakarta"))
    body = parse_rss(rss(("CPI", jakarta), ("NFP", T0 + timedelta(minutes=1))))
    assert [i.id for i in body.items] == ["CPI", "NFP"]
    assert body.items[0].published == T0
    assert all(i.published and i.published.utcoffset() == timedelta(0) for i in body.items)


def test_parse_rss_rejects_non_feeds() -> None:
    with pytest.raises(FormatError):
        parse_rss(b"<html><body>not a feed")


def test_parse_json_items_follows_dot_path_and_time_format() -> None:
    payload = {"data": {"rows": [{"t": 1792024200, "h": "a"}, {"t": 1792024260, "h": "b"}]}}
    body = parse_json_items(
        json.dumps(payload).encode(),
        source(
            type="json",
            parse={
                "items": "data.rows",
                "time_field": "t",
                "time_format": "unix",
                "title_field": "h",
            },
        ).parse,
    )
    assert [i.title for i in body.items] == ["a", "b"]
    assert body.items[0].published == datetime.fromtimestamp(1792024200, UTC)
    assert body.notes == []


def test_parse_json_items_flags_zone_less_timestamps_as_assumed_utc() -> None:
    payload = [{"d": "2026-10-13T08:30:00"}, {"d": "2026-10-13T08:30:00-04:00"}]
    spec = source(type="json", parse={"time_field": "d", "time_format": "iso"}).parse
    body = parse_json_items(json.dumps(payload).encode(), spec)
    assert body.items[1].published == datetime(2026, 10, 13, 12, 30, tzinfo=UTC)
    assert any("diasumsikan UTC" in note for note in body.notes)


def test_parse_json_items_rejects_wrong_item_path() -> None:
    spec = source(type="json", parse={"items": "missing"}).parse
    with pytest.raises(FormatError):
        parse_json_items(b'{"articles": []}', spec)


def test_parse_ics_reads_utc_and_flags_tzid_events() -> None:
    ics = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261014T123000Z\nEND:VEVENT\n"
        "BEGIN:VEVENT\nDTSTART;TZID=US-Eastern:20261015T083000\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    body = parse_ics(ics.encode())
    assert body.items[0].published == datetime(2026, 10, 14, 12, 30, tzinfo=UTC)
    assert any("TZID" in note for note in body.notes)


# --------------------------------------------------------------------------- HTTP checks


async def test_http_check_measures_freshness_caching_and_robots() -> None:
    clock = FakeClock(T0 + timedelta(minutes=5))
    feed = rss(("Fed holds", T0), ("Gold up", T0 - timedelta(minutes=20)))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=feed, headers={"ETag": '"v1"'})

    async with client_for(handler) as client:
        result = await check_http_source(
            client, source(), {}, robots=RobotsCache("ua"), clock=clock
        )
    assert result.status == "ok"
    assert result.item_count == 2
    assert result.newest_published == T0
    assert result.newest_age_seconds == 300.0
    assert result.timestamp_precision == "minute"
    assert result.has_etag is True
    assert result.conditional_304 is True
    assert result.robots == "allowed"


async def test_http_check_fails_when_robots_disallows_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /feed\n")
        return httpx.Response(200, content=rss(("x", T0)))

    async with client_for(handler) as client:
        result = await check_http_source(
            client, source(), {}, robots=RobotsCache("ua"), clock=FakeClock(T0)
        )
    assert result.status == "fail"
    assert result.robots == "disallowed"


async def test_missing_robots_txt_means_no_restrictions_rfc9309() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=rss(("x", T0)))

    async with client_for(handler) as client:
        result = await check_http_source(
            client, source(), {}, robots=RobotsCache("ua"), clock=FakeClock(T0)
        )
    assert result.robots == "no_robots"
    assert result.status == "ok"


async def test_stale_or_empty_news_feeds_are_flagged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/empty":
            return httpx.Response(200, content=rss())
        return httpx.Response(200, content=rss(("old", T0 - timedelta(days=2))))

    async with client_for(handler) as client:
        stale = await check_http_source(
            client, source(), {}, robots=RobotsCache("ua"), clock=FakeClock(T0)
        )
        empty = await check_http_source(
            client,
            source(url="https://news.example.com/empty"),
            {},
            robots=RobotsCache("ua"),
            clock=FakeClock(T0),
        )
    assert (stale.status, empty.status) == ("warn", "warn")


async def test_http_errors_and_network_failures_are_reported_as_failures() -> None:
    def server_error(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with client_for(server_error) as client:
        failed = await check_http_source(
            client, source(), {}, robots=RobotsCache("ua"), clock=FakeClock(T0)
        )
    async with client_for(unreachable) as client:
        down = await check_http_source(
            client, source(), {}, robots=RobotsCache("ua"), clock=FakeClock(T0)
        )
    assert (failed.status, failed.reason) == ("fail", "HTTP 503")
    assert down.status == "fail"
    assert "gagal konek" in down.reason
    assert down.robots == "unknown"


async def test_keyed_sources_are_skipped_without_key_and_never_leak_it() -> None:
    keyed = source(
        type="json",
        kind="macro",
        url="https://api.example.com/obs?api_key=${FRED_API_KEY}",
        requires_env=["FRED_API_KEY"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"ok": True})

    async with client_for(handler) as client:
        skipped = await check_http_source(
            client, keyed, {}, robots=RobotsCache("ua"), clock=FakeClock(T0)
        )
        used = await check_http_source(
            client,
            keyed,
            {"FRED_API_KEY": "real-fred-key"},
            robots=RobotsCache("ua"),
            clock=FakeClock(T0),
        )
    assert skipped.status == "skipped"
    assert "FRED_API_KEY" in skipped.reason
    assert used.status == "ok"
    assert "real-fred-key" not in used.model_dump_json()


def test_telegram_source_waits_for_channel_choice() -> None:
    telegram = source(type="telegram", url=None, channels=[], requires_env=["TELEGRAM_API_ID"])
    result = check_telegram_source(telegram, {}, FakeClock(T0))
    assert result.status == "skipped"
    assert "Q5" in result.reason


# --------------------------------------------------------------------------- watch mode


async def test_watch_mode_measures_lag_only_for_items_new_in_the_window() -> None:
    clock = FakeClock(T0)
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        polls += 1
        items = [("baseline", T0 - timedelta(minutes=10))]
        if polls >= 2:
            items.append(("fresh", clock.now - timedelta(seconds=25)))
            items.append(("future", clock.now + timedelta(seconds=60)))
        return httpx.Response(200, content=rss(*items))

    async with client_for(handler) as client:
        lags = await watch_lag(
            client,
            [source()],
            {},
            minutes=1,
            min_poll_seconds=10,
            clock=clock,
            sleep=clock.sleep,
        )
    stats = lags["feed"]
    assert stats.samples == 2
    assert stats.max_seconds == 25.0
    assert stats.negative == 1


async def test_run_checks_dispatches_every_source_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=rss(("x", T0)))

    sources = [
        source(id="feed_a"),
        source(id="tg", type="telegram", url=None),
        source(id="infra", kind="infra", type="html", url="https://api.example.com/"),
    ]
    results = await run_checks(
        sources,
        {},
        user_agent="ua",
        timeout_seconds=5,
        min_poll_seconds=10,
        clock=FakeClock(T0),
        transport=httpx.MockTransport(handler),
    )
    assert {r.source_id: r.status for r in results} == {
        "feed_a": "ok",
        "tg": "skipped",
        "infra": "ok",
    }


# --------------------------------------------------------------------------- websocket


async def test_websocket_lag_uses_the_exchange_event_time() -> None:
    from websockets.asyncio.server import ServerConnection, serve

    async def handler(socket: ServerConnection) -> None:
        for _ in range(3):
            await socket.send(json.dumps({"E": int(time.time() * 1000) - 50}))
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        declared = source(
            type="websocket",
            url="wss://stream.example.com/ws",
            parse={"time_field": "E", "time_format": "unix_ms"},
        )
        ws_source = declared.model_copy(update={"url": f"ws://127.0.0.1:{port}"})
        result = await check_websocket_source(ws_source, {}, sample_size=3, timeout_seconds=5)
    assert result.status == "ok"
    assert result.item_count == 3
    assert result.ws_lag is not None
    assert result.ws_lag.p50_seconds is not None
    assert 0.0 <= result.ws_lag.p50_seconds < 5.0

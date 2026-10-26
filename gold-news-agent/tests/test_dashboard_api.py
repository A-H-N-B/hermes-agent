"""Dashboard HTTP contract: login, cookie, rate limit, security headers, API payloads, WS auth."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.dashboard.app import create_app
from gold_agent.dashboard.auth import COOKIE, SessionSigner
from gold_agent.dashboard.live import LiveHub
from gold_agent.services.dashboard import UnsafeBind, check_bind, listen
from tests.dashboard_seed import HOSTILE, NOW, seed_dashboard

PASSWORD = "rahasia-uji"  # noqa: S105 - test-only password


@pytest.fixture
async def client(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    clock.set(NOW)
    await seed_dashboard(ctx, settings)
    app = create_app(ctx, LiveHub(), password=PASSWORD)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def login(http: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await http.post(
        "/login",
        content=f"password={password}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def test_open_bind_without_password_is_refused() -> None:
    check_bind("127.0.0.1", None)
    check_bind("0.0.0.0", "pw")  # noqa: S104 - the point of the test
    with pytest.raises(UnsafeBind):
        check_bind("0.0.0.0", None)  # noqa: S104


def test_a_second_instance_cannot_take_the_dashboard_port() -> None:
    first = listen("127.0.0.1", 0)
    try:
        with pytest.raises(OSError, match=r"(?i)address already in use|only one usage"):
            listen("127.0.0.1", first.getsockname()[1]).close()
    finally:
        first.close()


def test_session_tokens_expire_and_cannot_be_forged() -> None:
    signer = SessionSigner(PASSWORD)
    token = signer.issue(NOW, timedelta(hours=1))
    assert signer.valid(token, NOW + timedelta(minutes=59))
    assert not signer.valid(token, NOW + timedelta(hours=1, seconds=1))
    expires, nonce, mac = token.split(".")
    assert not signer.valid(f"{int(expires) + 3600}.{nonce}.{mac}", NOW)  # extended expiry
    assert not SessionSigner("password-lain").valid(token, NOW)  # rotated password
    assert not signer.valid("garbage", NOW)


async def test_everything_needs_a_login(client: httpx.AsyncClient) -> None:
    page = await client.get("/")
    assert page.status_code == 303 and page.headers["location"] == "/login"
    for path in ("/api/state", "/api/news", "/api/outlook", "/api/health", "/api/performance"):
        assert (await client.get(path)).status_code == 401, path
    assert (await client.get("/healthz")).json() == {"ok": True}


async def test_login_sets_a_strict_cookie_and_unlocks_the_api(client: httpx.AsyncClient) -> None:
    wrong = await login(client, "salah")
    assert wrong.status_code == 303 and wrong.headers["location"] == "/login?gagal=1"
    response = await login(client)
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Secure" not in cookie  # plain http here; behind an HTTPS tunnel it becomes Secure
    page = await client.get("/")
    assert page.status_code == 200 and "Gold News Agent" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe" not in csp
    assert page.headers["x-frame-options"] == "DENY"


async def test_an_update_reaches_the_browser_without_a_hard_refresh(
    client: httpx.AsyncClient,
) -> None:
    await login(client)
    for path in ("/", "/static/app.js", "/static/style.css"):
        response = await client.get(path)
        assert response.headers["cache-control"] == "no-cache", path  # revalidated every load
    etag = (await client.get("/static/app.js")).headers["etag"]
    unchanged = await client.get("/static/app.js", headers={"if-none-match": etag})
    assert unchanged.status_code == 304  # an unchanged file is not sent again


async def test_https_proxy_makes_the_cookie_secure(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/login",
        content=f"password={PASSWORD}",
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-forwarded-proto": "https",
        },
    )
    assert "Secure" in response.headers["set-cookie"]


async def test_repeated_failures_are_rate_limited(client: httpx.AsyncClient) -> None:
    for _ in range(5):
        assert (await login(client, "tebak")).status_code == 303
    blocked = await login(client)  # even the right password waits out the window
    assert blocked.status_code == 429


async def test_api_payloads(client: httpx.AsyncClient) -> None:
    await login(client)
    state = (await client.get("/api/state")).json()
    assert state["active"]["action"] == "BUY" and state["state"]["action"] == "BUY"
    assert state["context"]["session"] == "London–New York"
    assert state["next_event"]["title"] == "CPI m/m"
    candles = (await client.get("/api/candles?tf=M5")).json()
    assert len(candles) == 60 and {"time", "open", "high", "low", "close"} <= candles[0].keys()
    assert (await client.get("/api/candles?tf=X9")).status_code == 422
    news = (await client.get("/api/news")).json()
    assert [n["id"] for n in news] == ["c1", "c2"]
    assert news[1]["summary"] == HOSTILE  # raw in JSON; the page renders it as text
    markers = (await client.get("/api/markers")).json()
    assert [s["action"] for s in markers["signals"]] == ["BUY"] and len(markers["news"]) == 2
    outlook = (await client.get("/api/outlook")).json()
    assert len(outlook["rows"]) == 24 and outlook["trigger"] == "pembukaan sesi New York"
    calendar = (await client.get("/api/calendar")).json()
    assert calendar[0]["title"] == "CPI m/m" and len(calendar[0]["playbook"]["buckets"]) == 5
    performance = (await client.get("/api/performance")).json()
    assert performance["n_signals"] == 1 and performance["notes"][0].startswith("sampel belum")
    health = (await client.get("/api/health")).json()
    by_source = {s["source_id"]: s for s in health["sources"]}
    assert by_source["fed_press_all"]["lag_p50"] == 4.0
    assert by_source["bls_latest"]["status"] == "down"
    assert [a["title"] for a in health["alerts"]] == ["Sumber bls_latest mati"]
    assert health["heartbeats"]["signal"]["age_seconds"] == 0.0
    assert health["stages"][0]["stage"] == "signal"


async def test_websocket_requires_the_session_and_same_origin(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> None:
    clock.set(NOW)
    app = create_app(ctx, LiveHub(), password=PASSWORD)
    signer = SessionSigner(PASSWORD)
    with TestClient(app) as http:
        with pytest.raises(WebSocketDisconnect), http.websocket_connect("/ws") as ws:
            ws.receive_text()
        http.cookies.set(COOKIE, signer.issue(NOW, timedelta(hours=1)))
        evil = {"origin": "https://evil.example"}
        with pytest.raises(WebSocketDisconnect), http.websocket_connect("/ws", headers=evil) as ws:
            ws.receive_text()
        with http.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
            ws.send_text("ping")  # accepted: the connection stays open

"""FastAPI dashboard (section 16): six pages in one static app, JSON APIs, a live WebSocket.

Security (C2.4): bind to localhost unless a password is set; HMAC session cookie (HttpOnly,
SameSite=Strict, Secure behind HTTPS); failed-login rate limit; strict CSP (the front end has no
inline script and no eval); every external string is rendered with textContent.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect

from gold_agent.core.app import AppContext
from gold_agent.dashboard.auth import COOKIE, LoginLimiter, SessionSigner
from gold_agent.dashboard.credit import credit_view, set_balance
from gold_agent.dashboard.data import DashboardData
from gold_agent.dashboard.live import LiveHub
from gold_agent.market.rebase import request_calibration

STATIC_DIR = Path(__file__).parent / "static"  # public assets (JS, CSS, vendored chart)
PAGES_DIR = Path(__file__).parent / "pages"  # HTML shells, served by the routes below
MAX_LOGIN_BODY = 4096
MAX_CREDIT_USD = 100_000.0


# The TradingView embed script, and the iframe it creates (both hosts it has used).
TRADINGVIEW_SCRIPT = "https://s3.tradingview.com"
TRADINGVIEW_FRAMES = "https://www.tradingview-widget.com https://s.tradingview.com"


def _csp(host: str, *, tradingview: bool) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' {TRADINGVIEW_SCRIPT}" if tradingview else "script-src 'self'",
            f"frame-src {TRADINGVIEW_FRAMES}" if tradingview else "frame-src 'none'",
            "style-src 'self'",
            "img-src 'self' data:",
            f"connect-src 'self' ws://{host} wss://{host}",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self'",
        ]
    )


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"


def create_app(ctx: AppContext, hub: LiveHub, *, password: str | None) -> FastAPI:
    settings = ctx.settings.dashboard
    signer = SessionSigner(password) if password else None
    limiter = LoginLimiter(
        settings.login_attempts_per_window, timedelta(minutes=settings.login_window_minutes)
    )
    data = DashboardData(ctx, hub)
    tradingview = settings.tradingview_symbol is not None
    app = FastAPI(title="Gold News Agent", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _csp(
            request.headers.get("host", "localhost"), tradingview=tradingview
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def authed(token: str | None) -> bool:
        return signer is None or signer.valid(token, ctx.clock.now())

    async def require_login(request: Request) -> None:
        if not authed(request.cookies.get(COOKIE)):
            raise HTTPException(status_code=401, detail="login diperlukan")

    # ------------------------------------------------------------------ pages + login

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/", response_model=None)
    async def index(request: Request) -> Response:
        if not authed(request.cookies.get(COOKIE)):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(PAGES_DIR / "index.html")

    @app.get("/login", response_model=None)
    async def login_page() -> Response:
        if signer is None:
            return RedirectResponse("/", status_code=303)
        return FileResponse(PAGES_DIR / "login.html")

    @app.post("/login", response_model=None)
    async def login(request: Request) -> Response:
        if signer is None:
            return RedirectResponse("/", status_code=303)
        now = ctx.clock.now()
        client = _client(request)
        if limiter.blocked(client, now):
            return PlainTextResponse("Terlalu banyak percobaan. Coba lagi nanti.", status_code=429)
        body = (await request.body())[:MAX_LOGIN_BODY].decode("utf-8", errors="replace")
        attempt = (parse_qs(body).get("password") or [""])[0]
        if not signer.check_password(attempt):
            limiter.failed(client, now)
            return RedirectResponse("/login?gagal=1", status_code=303)
        limiter.succeeded(client)
        lifetime = timedelta(hours=settings.session_hours)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE,
            signer.issue(now, lifetime),
            max_age=int(lifetime.total_seconds()),
            httponly=True,
            samesite="strict",
            secure=_https(request),
            path="/",
        )
        return response

    @app.post("/logout", response_model=None)
    async def logout() -> Response:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    # ------------------------------------------------------------------ APIs

    guard = [Depends(require_login)]

    @app.get("/api/state", dependencies=guard)
    async def api_state() -> dict[str, Any]:
        return await data.state()

    @app.get("/api/candles", dependencies=guard)
    async def api_candles(
        tf: str = Query(
            default=settings.chart_timeframe, pattern="^(M1|M5|M15|M30|H1|H4|D1|W1|MN)$"
        ),
        limit: int = Query(default=settings.chart_bars, gt=0, le=5000),
    ) -> list[dict[str, Any]]:
        return await data.candles(tf, limit)

    @app.get("/api/indicators", dependencies=guard)
    async def api_indicators(
        tf: str = Query(
            default=settings.chart_timeframe, pattern="^(M1|M5|M15|M30|H1|H4|D1|W1|MN)$"
        ),
        limit: int = Query(default=settings.chart_bars, gt=0, le=5000),
        series: bool = Query(default=True),
    ) -> dict[str, Any]:
        return await data.indicators(tf, limit, series=series)

    @app.get("/api/forecast", dependencies=guard)
    async def api_forecast(
        tf: str = Query(
            default=settings.chart_timeframe, pattern="^(M1|M5|M15|M30|H1|H4|D1|W1|MN)$"
        ),
    ) -> dict[str, Any]:
        return await data.forecast(tf)

    @app.get("/api/timing", dependencies=guard)
    async def api_timing() -> dict[str, Any]:
        return await data.timing()

    @app.get("/api/credit", dependencies=guard)
    async def api_credit() -> dict[str, Any]:
        return await credit_view(ctx)

    async def json_body(request: Request) -> Any:
        # Writes take same-origin JSON only, so another site cannot post them from a browser
        # (a cross-site JSON POST needs a CORS preflight this app never grants).
        origin = request.headers.get("origin")
        if origin is not None and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="origin lain ditolak")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(status_code=415, detail="kirim JSON")
        return await request.json()

    @app.post("/api/credit", dependencies=guard)
    async def api_set_credit(request: Request) -> dict[str, Any]:
        body = await json_body(request)
        balance = body.get("balance_usd") if isinstance(body, dict) else None
        if isinstance(balance, bool) or not isinstance(balance, int | float):
            raise HTTPException(status_code=422, detail="balance_usd harus angka")
        if not 0 <= balance <= MAX_CREDIT_USD:
            raise HTTPException(status_code=422, detail="saldo di luar batas wajar")
        await set_balance(ctx, float(balance))
        return await credit_view(ctx)

    @app.post("/api/price-offset", dependencies=guard)
    async def api_price_offset(request: Request) -> dict[str, Any]:
        """Re-base a proxy feed onto the broker bid the user sees (market/rebase.py);
        {"broker_price": null} goes back to the feed's own prices."""
        body = await json_body(request)
        price = body.get("broker_price", 0) if isinstance(body, dict) else 0
        if price is not None and (
            isinstance(price, bool) or not isinstance(price, int | float) or not 0 < price < 1e6
        ):
            raise HTTPException(status_code=422, detail="broker_price harus harga yang wajar")
        broker = float(price) if price is not None else None
        return {"result": await request_calibration(ctx, broker, by="dashboard")}

    @app.get("/api/markers", dependencies=guard)
    async def api_markers(hours: float = Query(default=24, gt=0, le=24 * 14)) -> dict[str, Any]:
        return await data.markers(hours)

    @app.get("/api/news", dependencies=guard)
    async def api_news(
        limit: int = Query(default=settings.news_limit, gt=0, le=500),
    ) -> list[dict[str, Any]]:
        return await data.news(limit)

    @app.get("/api/outlook", dependencies=guard)
    async def api_outlook() -> JSONResponse:
        return JSONResponse(await data.outlook())

    @app.get("/api/calendar", dependencies=guard)
    async def api_calendar(days: float = Query(default=7, gt=0, le=31)) -> list[dict[str, Any]]:
        return await data.calendar(days)

    @app.get("/api/performance", dependencies=guard)
    async def api_performance(days: float = Query(default=90, gt=0, le=3650)) -> dict[str, Any]:
        return await data.performance(days)

    @app.get("/api/health", dependencies=guard)
    async def api_health() -> dict[str, Any]:
        return await data.health()

    # ------------------------------------------------------------------ live

    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        same_origin = origin is None or urlparse(origin).netloc == ws.headers.get("host")
        if not same_origin or not authed(ws.cookies.get(COOKIE)):
            await ws.close(code=1008)  # policy violation: cross-site or not logged in
            return
        await ws.accept()
        hub.add(ws)
        try:
            while True:
                await ws.receive_text()  # the page never sends anything meaningful
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(ws)
            with contextlib.suppress(Exception):
                await ws.close()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app

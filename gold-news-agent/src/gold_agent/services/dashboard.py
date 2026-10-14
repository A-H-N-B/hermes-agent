"""Dashboard service: FastAPI app on an embedded uvicorn server plus the bus -> browser bridge.

Refuses to listen on a non-loopback address without DASHBOARD_PASSWORD (C2.4)."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from collections.abc import Awaitable, Callable, Generator
from typing import Any

import structlog
import uvicorn

from gold_agent.core.app import AppContext
from gold_agent.core.bus import (
    STREAM_ALERTS,
    STREAM_ANALYSES,
    STREAM_CONTEXT,
    STREAM_OUTLOOK,
    STREAM_SIGNALS,
    STREAM_STATE,
    STREAM_TICKS,
    Message,
    consume,
)
from gold_agent.dashboard.app import create_app
from gold_agent.dashboard.auth import is_loopback
from gold_agent.dashboard.live import LiveHub

log = structlog.get_logger(__name__)


class UnsafeBind(RuntimeError):
    pass


class _EmbeddedServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield  # the supervisor owns SIGTERM/SIGINT; run() sets should_exit on stop


def check_bind(host: str, password: str | None) -> None:
    if not is_loopback(host) and not password:
        raise UnsafeBind(
            f"dashboard.host={host} terbuka ke jaringan tanpa DASHBOARD_PASSWORD: isi password "
            "atau bind ke 127.0.0.1 dan akses lewat Tailscale / Cloudflare Tunnel / SSH tunnel"
        )


class DashboardService:
    name = "dashboard"

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        settings = ctx.settings.dashboard
        self._password = ctx.env.get("DASHBOARD_PASSWORD") or None
        check_bind(settings.host, self._password)
        self.hub = LiveHub(tick_per_second=settings.tick_push_per_second)
        self.app = create_app(ctx, self.hub, password=self._password)
        self.port = settings.port

    def _bridge(self, kind: str) -> Callable[[Message], Awaitable[None]]:
        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
            "tick": self.hub.on_tick,
            "context": self.hub.on_context,
            "news": self.hub.on_analysis,
        }
        handler = handlers.get(kind)

        async def forward(message: Message) -> None:
            if not self.hub.clients:
                return
            if handler is not None:
                await handler(message.data)
            else:
                await self.hub.forward(kind, message.data)

        return forward

    async def run(self, stop: asyncio.Event) -> None:
        settings = self._ctx.settings.dashboard
        sock = listen(settings.host, settings.port)  # OSError -> supervisor retries with backoff
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(
            self.app,
            log_config=None,
            access_log=False,
            lifespan="off",
            ws_ping_interval=20,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
        server = _EmbeddedServer(config)
        bus = self._ctx.bus
        streams = [
            (STREAM_TICKS, "tick"),
            (STREAM_CONTEXT, "context"),
            (STREAM_STATE, "state"),
            (STREAM_SIGNALS, "signal"),
            (STREAM_ANALYSES, "news"),
            (STREAM_ALERTS, "alert"),
            (STREAM_OUTLOOK, "outlook"),
        ]
        tasks = [
            asyncio.create_task(
                consume(
                    bus,
                    stream,
                    f"dashboard-{kind}",
                    "dashboard-1",
                    self._bridge(kind),
                    stop,
                    start="latest",
                )
            )
            for stream, kind in streams
        ]
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        stopping = asyncio.create_task(stop.wait())
        log.info(
            "dashboard.started", host=settings.host, port=self.port, login=bool(self._password)
        )
        try:
            await asyncio.wait({serving, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(serving, timeout=10)
            for task in [*tasks, stopping]:
                task.cancel()
            sock.close()
        if not stop.is_set():
            raise RuntimeError("dashboard server stopped unexpectedly")


def listen(host: str, port: int) -> socket.socket:
    """Bind ourselves: uvicorn's own bind calls sys.exit() on failure, which would take every
    service in this process down with it."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform == "win32":  # SO_REUSEADDR there would let a second process share the port
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:  # quick restarts past TIME_WAIT
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    sock.setblocking(False)
    return sock

"""Source verification (Phase 0): reachability, format, freshness, latency, caching, robots.

Every number in the report comes from a real request made by this module; nothing is estimated.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field

from gold_agent.collectors.parsers import (
    FeedItem,
    FormatError,
    ParsedBody,
    parse_body,
    parse_csv_items,
    parse_ics,
    parse_json_items,
    parse_rss,
    parse_timestamp,
    resolve_path,
    to_utc,
)
from gold_agent.config import ParseSpec, Source, SourceKind, SourceType, expand_url
from gold_agent.ops.redaction import Redactor
from gold_agent.stats import percentile

__all__ = [
    "FeedItem",
    "FormatError",
    "LagStats",
    "ParsedBody",
    "Redactor",
    "RobotsCache",
    "SourceCheck",
    "check_http_source",
    "check_telegram_source",
    "check_websocket_source",
    "lag_stats",
    "parse_csv_items",
    "parse_ics",
    "parse_json_items",
    "parse_rss",
    "run_checks",
    "timestamp_precision",
    "utc_now",
    "watch_lag",
]

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]
CheckStatus = Literal["ok", "warn", "fail", "skipped"]
Precision = Literal["second", "minute", "date", "none"]
RobotsState = Literal["allowed", "disallowed", "no_robots", "unknown", "n/a"]

_RECORD_SUFFIX = {
    SourceType.RSS: ".xml",
    SourceType.JSON: ".json",
    SourceType.CSV: ".csv",
    SourceType.ICS: ".ics",
    SourceType.HTML: ".html",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class LagStats(BaseModel):
    samples: int
    p50_seconds: float | None
    p95_seconds: float | None
    max_seconds: float | None
    negative: int = 0  # published after we saw it: clock or time-zone problem at the source


class SourceCheck(BaseModel):
    source_id: str
    name: str
    kind: SourceKind
    type: SourceType
    tier: int
    status: CheckStatus
    reason: str
    checked_at: datetime
    url: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    elapsed_ms: float | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    item_count: int | None = None
    newest_published: datetime | None = None
    oldest_published: datetime | None = None
    newest_age_seconds: float | None = None
    timestamp_precision: Precision | None = None
    has_etag: bool | None = None
    has_last_modified: bool | None = None
    conditional_304: bool | None = None
    robots: RobotsState = "n/a"
    server_clock_offset_seconds: float | None = None
    ws_lag: LagStats | None = None
    watch_lag: LagStats | None = None
    notes: list[str] = Field(default_factory=list)
    tos_note: str
    tos_url: str | None = None


# --------------------------------------------------------------------------- timing helpers


def timestamp_precision(times: Sequence[datetime]) -> Precision:
    """Coarsest precision consistent with every timestamp (all :00 seconds -> minute)."""
    if not times:
        return "none"
    if all(t.hour == 0 and t.minute == 0 and t.second == 0 for t in times):
        return "date"
    if all(t.second == 0 and t.microsecond == 0 for t in times):
        return "minute"
    return "second"


def lag_stats(samples: Sequence[float]) -> LagStats:
    if not samples:
        return LagStats(samples=0, p50_seconds=None, p95_seconds=None, max_seconds=None)
    return LagStats(
        samples=len(samples),
        p50_seconds=round(percentile(samples, 0.5), 3),
        p95_seconds=round(percentile(samples, 0.95), 3),
        max_seconds=round(max(samples), 3),
        negative=sum(1 for s in samples if s < 0),
    )


# --------------------------------------------------------------------------- robots.txt


class RobotsCache:
    """RFC 9309: 4xx robots.txt = no restrictions; 5xx / unreachable = unknown (treat as no)."""

    def __init__(self, user_agent: str) -> None:
        self._user_agent = user_agent
        self._entries: dict[str, RobotFileParser | RobotsState] = {}
        self._lock = asyncio.Lock()

    async def check(self, client: httpx.AsyncClient, url: str) -> RobotsState:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._lock:
            if origin not in self._entries:
                self._entries[origin] = await self._fetch(client, origin)
        entry = self._entries[origin]
        if isinstance(entry, RobotFileParser):
            return "allowed" if entry.can_fetch(self._user_agent, url) else "disallowed"
        return entry

    @staticmethod
    async def _fetch(client: httpx.AsyncClient, origin: str) -> RobotFileParser | RobotsState:
        try:
            response = await client.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            return "unknown"
        if response.status_code == 200:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            return parser
        if 400 <= response.status_code < 500:
            return "no_robots"
        return "unknown"


# --------------------------------------------------------------------------- checks


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return f"timeout ({type(exc).__name__})"
    if isinstance(exc, httpx.ProxyError):
        return f"ditolak proxy/kebijakan jaringan: {exc}"
    if isinstance(exc, httpx.ConnectError):
        return f"gagal konek: {exc}"
    return f"{type(exc).__name__}: {exc}"


def _base(source: Source, clock: Clock, redact: Redactor) -> dict[str, Any]:
    return {
        "source_id": source.id,
        "name": source.name,
        "kind": source.kind,
        "type": source.type,
        "tier": source.tier,
        "checked_at": clock(),
        "url": redact(source.url) if source.url else None,
        "tos_note": source.tos_note,
        "tos_url": source.tos_url,
    }


def _server_offset(response: httpx.Response, fetched_at: datetime) -> float | None:
    header = response.headers.get("date")
    if not header:
        return None
    try:
        server_time = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    return round((server_time - fetched_at).total_seconds(), 1)


def _freshness(body: ParsedBody, fetched_at: datetime) -> dict[str, Any]:
    times = [i.published for i in body.items if i.published is not None]
    if not times:
        return {"timestamp_precision": "none"}
    newest = max(times)
    return {
        "newest_published": newest,
        "oldest_published": min(times),
        "newest_age_seconds": round((fetched_at - newest).total_seconds(), 1),
        "timestamp_precision": timestamp_precision(times),
    }


def _verdict(
    source: Source, robots: RobotsState, item_count: int, age: float | None, stale_hours: float
) -> tuple[CheckStatus, str]:
    if robots == "disallowed":
        return "fail", "URL dilarang oleh robots.txt"
    if source.kind is SourceKind.NEWS and item_count == 0:
        return "warn", "terjangkau tetapi feed kosong"
    if source.kind is SourceKind.NEWS and age is not None and age > stale_hours * 3600:
        return "warn", f"item terbaru berumur {age / 3600:.1f} jam"
    if robots == "unknown":
        return "warn", "OK, tetapi robots.txt tidak bisa dibaca"
    return "ok", "OK"


def _write_recording(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


async def _conditional_supported(
    client: httpx.AsyncClient, url: str, response: httpx.Response
) -> bool | None:
    headers: dict[str, str] = {}
    if etag := response.headers.get("etag"):
        headers["If-None-Match"] = etag
    if last_modified := response.headers.get("last-modified"):
        headers["If-Modified-Since"] = last_modified
    if not headers:
        return None
    try:
        again = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return None
    return again.status_code == 304


async def check_http_source(
    client: httpx.AsyncClient,
    source: Source,
    env: dict[str, str],
    *,
    robots: RobotsCache,
    clock: Clock = utc_now,
    record_dir: Path | None = None,
    news_stale_hours: float = 6.0,
) -> SourceCheck:
    redact = Redactor(env.values())
    base = _base(source, clock, redact)
    try:
        url = expand_url(source.url or "", env)
    except KeyError as exc:
        return SourceCheck(**base, status="skipped", reason=f"butuh {exc.args[0]} di .env")

    robots_state: RobotsState = "n/a"
    if source.kind is not SourceKind.INFRA:
        robots_state = await robots.check(client, url)

    started = time.perf_counter()
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        return SourceCheck(
            **base, status="fail", reason=redact(describe_error(exc)), robots=robots_state
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    fetched_at = clock()
    fields: dict[str, Any] = {
        **base,
        "final_url": redact(str(response.url)),
        "http_status": response.status_code,
        "elapsed_ms": elapsed_ms,
        "content_type": response.headers.get("content-type"),
        "size_bytes": len(response.content),
        "has_etag": "etag" in response.headers,
        "has_last_modified": "last-modified" in response.headers,
        "robots": robots_state,
        "server_clock_offset_seconds": _server_offset(response, fetched_at),
    }
    if record_dir is not None and response.status_code < 400:
        target = record_dir / f"{source.id}{_RECORD_SUFFIX[source.type]}"
        await asyncio.to_thread(_write_recording, target, response.content)

    if source.kind is SourceKind.INFRA:
        return SourceCheck(
            **fields, status="ok", reason=f"terjangkau (HTTP {response.status_code})"
        )
    if response.status_code >= 400:
        return SourceCheck(**fields, status="fail", reason=f"HTTP {response.status_code}")
    try:
        body = parse_body(source, response.content)
    except FormatError as exc:
        return SourceCheck(**fields, status="fail", reason=redact(str(exc)))

    fields.update(_freshness(body, fetched_at))
    fields["item_count"] = len(body.items)
    fields["notes"] = body.notes
    fields["conditional_304"] = await _conditional_supported(client, url, response)
    status, reason = _verdict(
        source, robots_state, len(body.items), fields.get("newest_age_seconds"), news_stale_hours
    )
    return SourceCheck(**fields, status=status, reason=reason)


async def check_websocket_source(
    source: Source,
    env: dict[str, str],
    *,
    clock: Clock = utc_now,
    sample_size: int = 20,
    timeout_seconds: float = 15.0,
) -> SourceCheck:
    from websockets.asyncio.client import connect
    from websockets.exceptions import WebSocketException

    redact = Redactor(env.values())
    base = _base(source, clock, redact)
    try:
        url = expand_url(source.url or "", env)
    except KeyError as exc:
        return SourceCheck(**base, status="skipped", reason=f"butuh {exc.args[0]} di .env")
    spec = source.parse or ParseSpec()
    lags: list[float] = []
    messages = 0
    try:
        async with asyncio.timeout(timeout_seconds), connect(url) as socket:
            async for raw in socket:
                received = clock()
                messages += 1
                if spec.time_field:
                    decoded: Any = json.loads(raw)
                    payload = resolve_path(decoded, spec.items) if spec.items else decoded
                    sent = (
                        parse_timestamp(payload.get(spec.time_field), spec.time_format)
                        if isinstance(payload, dict)
                        else None
                    )
                    if sent is not None:
                        lags.append((received - to_utc(sent)[0]).total_seconds())
                if messages >= sample_size:
                    break
    except TimeoutError:
        if messages == 0:
            return SourceCheck(**base, status="fail", reason="timeout: tidak ada pesan diterima")
    except (OSError, WebSocketException, json.JSONDecodeError) as exc:
        return SourceCheck(**base, status="fail", reason=redact(describe_error(exc)))
    return SourceCheck(
        **base,
        status="ok",
        reason=f"{messages} pesan diterima",
        item_count=messages,
        ws_lag=lag_stats(lags),
    )


def _skipped(source: Source, clock: Clock, reason: str) -> SourceCheck:
    return SourceCheck(**_base(source, clock, Redactor(())), status="skipped", reason=reason)


def check_telegram_source(source: Source, env: dict[str, str], clock: Clock) -> SourceCheck:
    if not source.channels:
        return _skipped(source, clock, "belum ada channel dipilih (pertanyaan Q5)")
    missing = [name for name in source.requires_env if not env.get(name)]
    if missing:
        return _skipped(source, clock, f"butuh {', '.join(missing)} di .env")
    return _skipped(source, clock, "diverifikasi oleh collector Telethon di Fase 1 (login sekali)")


# --------------------------------------------------------------------------- watch mode


async def _watch_one(
    client: httpx.AsyncClient,
    source: Source,
    url: str,
    *,
    until: datetime,
    interval: float,
    clock: Clock,
    sleep: Sleep,
) -> LagStats:
    seen: set[str] = set()
    samples: list[float] = []
    validators: dict[str, str] = {}
    baseline = True
    while clock() < until:
        try:
            response = await client.get(url, headers=validators)
        except httpx.HTTPError:
            await sleep(interval)
            continue
        first_seen = clock()
        if response.status_code == 200:
            if etag := response.headers.get("etag"):
                validators["If-None-Match"] = etag
            if last_modified := response.headers.get("last-modified"):
                validators["If-Modified-Since"] = last_modified
            try:
                items = parse_body(source, response.content).items
            except FormatError:
                items = []
            for item in items:
                if item.id in seen:
                    continue
                seen.add(item.id)
                if not baseline and item.published is not None:
                    samples.append((first_seen - item.published).total_seconds())
            baseline = False
        await sleep(interval)
    return lag_stats(samples)


async def watch_lag(
    client: httpx.AsyncClient,
    sources: Sequence[Source],
    env: dict[str, str],
    *,
    minutes: float,
    min_poll_seconds: float,
    clock: Clock = utc_now,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, LagStats]:
    """Poll each source for `minutes`; lag = first_seen - published for items new in the window.

    The measured lag includes up to one polling interval on top of the source's own delay.
    """
    until = clock() + timedelta(minutes=minutes)
    tasks = {
        source.id: _watch_one(
            client,
            source,
            expand_url(source.url or "", env),
            until=until,
            interval=max(source.poll_seconds or min_poll_seconds, min_poll_seconds),
            clock=clock,
            sleep=sleep,
        )
        for source in sources
    }
    results = await asyncio.gather(*tasks.values())
    return dict(zip(tasks, results, strict=True))


# --------------------------------------------------------------------------- orchestration


async def run_checks(
    sources: Sequence[Source],
    env: dict[str, str],
    *,
    user_agent: str,
    timeout_seconds: float,
    min_poll_seconds: float,
    watch_minutes: float = 0.0,
    record_dir: Path | None = None,
    clock: Clock = utc_now,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[SourceCheck]:
    limit = asyncio.Semaphore(8)
    robots = RobotsCache(user_agent)
    async with httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        timeout=timeout_seconds,
        follow_redirects=True,
        transport=transport,
    ) as client:

        async def one(source: Source) -> SourceCheck:
            async with limit:
                if source.type is SourceType.TELEGRAM:
                    return check_telegram_source(source, env, clock)
                if source.type is SourceType.WEBSOCKET:
                    return await check_websocket_source(
                        source, env, clock=clock, timeout_seconds=timeout_seconds
                    )
                return await check_http_source(
                    client, source, env, robots=robots, clock=clock, record_dir=record_dir
                )

        results = list(await asyncio.gather(*(one(s) for s in sources)))
        if watch_minutes <= 0:
            return results
        healthy = {r.source_id for r in results if r.status in ("ok", "warn")}
        watchable = [
            s
            for s in sources
            if s.id in healthy
            and s.kind is SourceKind.NEWS
            and s.type in (SourceType.RSS, SourceType.JSON)
        ]
        lags = await watch_lag(
            client,
            watchable,
            env,
            minutes=watch_minutes,
            min_poll_seconds=min_poll_seconds,
            clock=clock,
        )
    return [
        r.model_copy(update={"watch_lag": lags[r.source_id]}) if r.source_id in lags else r
        for r in results
    ]

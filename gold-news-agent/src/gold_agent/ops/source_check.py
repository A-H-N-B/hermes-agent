"""Source verification (Phase 0): reachability, format, freshness, latency, caching, robots.

Every number in the report comes from a real request made by this module; nothing is estimated.
"""

from __future__ import annotations

import asyncio
import calendar
import csv
import io
import json
import math
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import feedparser  # pyright: ignore[reportMissingTypeStubs]
import httpx
from pydantic import BaseModel, Field

from gold_agent.config import ParseSpec, Source, SourceKind, SourceType, expand_url

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
_SECRET_QUERY = re.compile(r"(?i)\b(api_?key|apikey|token|access_token|key)=([^&\s\"']+)")


def utc_now() -> datetime:
    return datetime.now(UTC)


class FormatError(ValueError):
    """The body was fetched but is not the format the source claims."""


class FeedItem(BaseModel):
    id: str
    title: str | None = None
    published: datetime | None = None


class ParsedBody(BaseModel):
    items: list[FeedItem]
    notes: list[str] = Field(default_factory=list)


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


# --------------------------------------------------------------------------- redaction


class Redactor:
    """Removes secret values (and secret-looking query params) from any text we emit."""

    def __init__(self, secrets: Iterable[str]) -> None:
        self._secrets = sorted((s for s in secrets if len(s) >= 4), key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return _SECRET_QUERY.sub(lambda m: f"{m.group(1)}=***", text)


# --------------------------------------------------------------------------- time helpers


def to_utc(moment: datetime) -> tuple[datetime, bool]:
    """Return (aware UTC datetime, whether the zone had to be assumed)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC), True
    return moment.astimezone(UTC), False


def parse_timestamp(value: object, fmt: str) -> datetime | None:
    """Parse a source timestamp; returns a naive datetime when the source gave no zone."""
    if value is None or value == "":
        return None
    text = str(value)
    if fmt == "unix":
        return datetime.fromtimestamp(float(text), UTC)
    if fmt == "unix_ms":
        return datetime.fromtimestamp(float(text) / 1000, UTC)
    if fmt == "iso":
        return datetime.fromisoformat(text)
    if fmt == "date":
        return datetime.strptime(text, "%Y-%m-%d")  # noqa: DTZ007 - zone resolved by caller
    return datetime.strptime(text, fmt)  # noqa: DTZ007 - zone resolved by caller


def timestamp_precision(times: Sequence[datetime]) -> Precision:
    """Coarsest precision consistent with every timestamp (all :00 seconds -> minute)."""
    if not times:
        return "none"
    if all(t.hour == 0 and t.minute == 0 and t.second == 0 for t in times):
        return "date"
    if all(t.second == 0 and t.microsecond == 0 for t in times):
        return "minute"
    return "second"


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q
    low = math.floor(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


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


# --------------------------------------------------------------------------- body parsers


def _resolve(data: Any, path: str) -> Any:
    node = data
    for part in filter(None, path.split(".")):
        if not isinstance(node, dict):
            return None
        node = cast(dict[str, Any], node).get(part)
    return node


def _items_with_times(
    raw: Iterable[tuple[str, str | None, datetime | None]],
) -> ParsedBody:
    items: list[FeedItem] = []
    assumed = 0
    for item_id, title, moment in raw:
        published = None
        if moment is not None:
            published, was_assumed = to_utc(moment)
            assumed += was_assumed
        items.append(FeedItem(id=item_id, title=title, published=published))
    notes = [f"{assumed} timestamp tanpa zona waktu, diasumsikan UTC"] if assumed else []
    return ParsedBody(items=items, notes=notes)


def parse_rss(content: bytes) -> ParsedBody:
    parsed = cast(Any, feedparser.parse(content))
    entries = cast(list[Any], parsed.entries)
    if parsed.bozo and not entries:
        raise FormatError(f"bukan RSS/Atom yang valid: {parsed.bozo_exception}")
    items: list[FeedItem] = []
    for index, entry in enumerate(entries):
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(struct), UTC) if struct else None
        title = entry.get("title")
        item_id = entry.get("id") or entry.get("link") or title or str(index)
        items.append(FeedItem(id=str(item_id), title=title, published=published))
    notes = ["feed punya peringatan parser (bozo) tapi tetap terbaca"] if parsed.bozo else []
    return ParsedBody(items=items, notes=notes)


def parse_json_items(content: bytes, spec: ParseSpec | None) -> ParsedBody:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FormatError(f"bukan JSON yang valid: {exc}") from exc
    spec = spec or ParseSpec()
    node: Any = _resolve(data, spec.items)
    if isinstance(node, dict):
        entries: list[Any] = [node]
    elif isinstance(node, list):
        entries = cast(list[Any], node)
    else:
        raise FormatError(f"path item '{spec.items}' bukan list")
    raw: list[tuple[str, str | None, datetime | None]] = []
    unparsable = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        row = cast(dict[str, Any], entry)
        moment = None
        if spec.time_field:
            try:
                moment = parse_timestamp(row.get(spec.time_field), spec.time_format)
            except (ValueError, OverflowError):
                unparsable += 1
        title = (
            str(row[spec.title_field]) if spec.title_field and row.get(spec.title_field) else None
        )
        item_id = row.get(spec.id_field) if spec.id_field else None
        raw.append((str(item_id or title or index), title, moment))
    body = _items_with_times(raw)
    if unparsable:
        body.notes.append(f"{unparsable} timestamp tidak bisa dibaca ({spec.time_format})")
    return body


def parse_csv_items(content: bytes, spec: ParseSpec | None) -> ParsedBody:
    text = content.decode("utf-8-sig", errors="replace")
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        raise FormatError("CSV kosong")
    header, data_rows = rows[0], rows[1:]
    column = header.index(spec.time_field) if spec and spec.time_field in header else None
    raw: list[tuple[str, str | None, datetime | None]] = []
    for index, row in enumerate(data_rows):
        moment = None
        if spec and column is not None and column < len(row):
            try:
                moment = parse_timestamp(row[column].strip(), spec.time_format)
            except ValueError:
                moment = None
        raw.append((str(index), None, moment))
    return _items_with_times(raw)


_ICS_START = re.compile(r"^DTSTART(?P<params>;[^:]*)?:(?P<value>\d{8}(?:T\d{6}Z?)?)\s*$", re.M)


def parse_ics(content: bytes) -> ParsedBody:
    text = content.decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        raise FormatError("bukan iCalendar (tidak ada BEGIN:VCALENDAR)")
    raw: list[tuple[str, str | None, datetime | None]] = []
    zoned = 0
    for index, match in enumerate(_ICS_START.finditer(text)):
        value, params = match.group("value"), match.group("params") or ""
        fmt = "%Y%m%dT%H%M%SZ" if value.endswith("Z") else "%Y%m%dT%H%M%S"
        if len(value) == 8:
            fmt = "%Y%m%d"
        moment = datetime.strptime(value, fmt)  # noqa: DTZ007 - zone resolved below
        if value.endswith("Z"):
            moment = moment.replace(tzinfo=UTC)
        elif "TZID" in params:
            zoned += 1
        raw.append((str(index), None, moment))
    body = _items_with_times(raw)
    if zoned:
        body.notes.append(f"{zoned} event memakai TZID; zona perlu di-resolve di parser Fase 2")
    return body


def parse_body(source: Source, content: bytes) -> ParsedBody:
    match source.type:
        case SourceType.RSS:
            return parse_rss(content)
        case SourceType.JSON:
            return parse_json_items(content, source.parse)
        case SourceType.CSV:
            return parse_csv_items(content, source.parse)
        case SourceType.ICS:
            return parse_ics(content)
        case _:
            return ParsedBody(items=[])


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
                    payload = cast(dict[str, Any], json.loads(raw))
                    sent = parse_timestamp(payload.get(spec.time_field), spec.time_format)
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

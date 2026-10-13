"""Message bus: Redis Streams in production, an in-process equivalent for tests/replay.

Delivery is at-least-once: handlers must be idempotent (every DB write is keyed by id).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel

STREAM_NEWS_RAW = "news.raw"  # NewsCluster events after normalise + dedup
STREAM_ANALYSES = "news.analyses"  # Analysis
STREAM_RELEASES = "news.releases"  # ReleaseReading (fast path)
STREAM_TICKS = "market.ticks"  # Tick (also fed by the Windows MT5 bridge)
STREAM_CONTEXT = "market.context"  # MarketContext snapshots
STREAM_SIGNALS = "signals"  # Signal
STREAM_STATE = "signals.state"  # SignalState
STREAM_ALERTS = "ops.alerts"  # Alert
STREAM_NOTIFY = "notify.outbox"  # Notification
STREAM_EVENTS = "app.events"  # small control events: {"type": "outlook.refresh", ...}
STREAM_DEADLETTER = "deadletter"

DEFAULT_MAXLEN: dict[str, int] = {
    STREAM_TICKS: 200_000,
    STREAM_CONTEXT: 20_000,
}
FALLBACK_MAXLEN = 50_000

StartAt = Literal["earliest", "latest"]
log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Message:
    id: str
    stream: str
    data: dict[str, Any]


def encode(payload: BaseModel | Mapping[str, Any]) -> str:
    if isinstance(payload, BaseModel):
        return payload.model_dump_json()
    return json.dumps(dict(payload), default=str)


class Bus(Protocol):
    async def publish(self, stream: str, payload: BaseModel | Mapping[str, Any]) -> str: ...

    async def read(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 50,
        block_ms: int = 1000,
        start: StartAt = "earliest",
    ) -> list[Message]: ...

    async def ack(self, stream: str, group: str, ids: Sequence[str]) -> None: ...

    async def lag(self, stream: str, group: str) -> int | None: ...

    async def close(self) -> None: ...


# --------------------------------------------------------------------------- in-process


@dataclass
class _Stream:
    entries: list[tuple[int, dict[str, Any]]] = field(
        default_factory=lambda: list[tuple[int, dict[str, Any]]]()
    )
    base: int = 0  # absolute offset of entries[0]
    next_offset: int = 0
    cursors: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class MemoryBus:
    """Single-process bus with consumer groups. Each group sees every message once."""

    def __init__(self, maxlen: int = 20_000) -> None:
        self._streams: dict[str, _Stream] = {}
        self._maxlen = maxlen

    def _stream(self, name: str) -> _Stream:
        return self._streams.setdefault(name, _Stream())

    async def publish(self, stream: str, payload: BaseModel | Mapping[str, Any]) -> str:
        state = self._stream(stream)
        offset = state.next_offset
        state.next_offset += 1
        state.entries.append((offset, json.loads(encode(payload))))
        overflow = len(state.entries) - self._maxlen
        if overflow > 0:
            del state.entries[:overflow]
            state.base += overflow
        state.changed.set()
        return str(offset)

    async def read(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 50,
        block_ms: int = 1000,
        start: StartAt = "earliest",
    ) -> list[Message]:
        state = self._stream(stream)
        if group not in state.cursors:
            state.cursors[group] = state.base if start == "earliest" else state.next_offset
        if state.cursors[group] >= state.next_offset and block_ms > 0:
            state.changed.clear()
            try:
                await asyncio.wait_for(state.changed.wait(), timeout=block_ms / 1000)
            except TimeoutError:
                return []
        cursor = max(state.cursors[group], state.base)
        index = cursor - state.base
        batch = state.entries[index : index + count]
        if batch:
            state.cursors[group] = batch[-1][0] + 1
        return [Message(id=str(offset), stream=stream, data=data) for offset, data in batch]

    async def ack(self, stream: str, group: str, ids: Sequence[str]) -> None:
        return None

    async def lag(self, stream: str, group: str) -> int | None:
        state = self._stream(stream)
        cursor = state.cursors.get(group)
        return None if cursor is None else state.next_offset - cursor

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------------- Redis Streams


class RedisBus:
    def __init__(self, url: str, maxlen: Mapping[str, int] | None = None) -> None:
        import redis.asyncio as redis_async

        self._redis: Any = redis_async.from_url(url, decode_responses=True)
        self._maxlen = {**DEFAULT_MAXLEN, **(maxlen or {})}
        self._groups: set[tuple[str, str]] = set()
        self._claimed: set[tuple[str, str, str]] = set()

    async def publish(self, stream: str, payload: BaseModel | Mapping[str, Any]) -> str:
        maxlen = self._maxlen.get(stream, FALLBACK_MAXLEN)
        message_id = await self._redis.xadd(
            stream, {"data": encode(payload)}, maxlen=maxlen, approximate=True
        )
        return str(message_id)

    async def _ensure_group(self, stream: str, group: str, start: StartAt) -> None:
        if (stream, group) in self._groups:
            return
        from redis.exceptions import ResponseError

        try:
            await self._redis.xgroup_create(
                stream, group, id="0" if start == "earliest" else "$", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._groups.add((stream, group))

    @staticmethod
    def _decode(stream: str, entries: Sequence[Any]) -> list[Message]:
        messages: list[Message] = []
        for message_id, fields in entries:
            if not fields:  # deleted by MAXLEN trimming while pending
                continue
            messages.append(Message(id=message_id, stream=stream, data=json.loads(fields["data"])))
        return messages

    async def read(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 50,
        block_ms: int = 1000,
        start: StartAt = "earliest",
    ) -> list[Message]:
        await self._ensure_group(stream, group, start)
        key = (stream, group, consumer)
        if key not in self._claimed:
            # After a crash, take over messages another consumer read but never acked.
            self._claimed.add(key)
            claimed = await self._redis.xautoclaim(
                stream, group, consumer, min_idle_time=60_000, start_id="0-0", count=count
            )
            recovered = self._decode(stream, claimed[1])
            if recovered:
                log.warning("bus.reclaimed", stream=stream, group=group, count=len(recovered))
                return recovered
        response = await self._redis.xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block_ms or None
        )
        if not response:
            return []
        _, entries = response[0]
        return self._decode(stream, entries)

    async def ack(self, stream: str, group: str, ids: Sequence[str]) -> None:
        if ids:
            await self._redis.xack(stream, group, *ids)

    async def lag(self, stream: str, group: str) -> int | None:
        try:
            groups = await self._redis.xinfo_groups(stream)
        except Exception:
            return None
        for info in groups:
            if info.get("name") == group:
                lag = info.get("lag")
                pending = info.get("pending") or 0
                return int(lag) + int(pending) if lag is not None else int(pending)
        return None

    async def close(self) -> None:
        await self._redis.aclose()


def make_bus(kind: str, redis_url: str | None) -> Bus:
    if kind == "memory":
        return MemoryBus()
    if not redis_url:
        raise ValueError("bus 'redis' needs REDIS_URL")
    return RedisBus(redis_url)


# --------------------------------------------------------------------------- consumer loop

Handler = Callable[[Message], Awaitable[None]]


async def consume(
    bus: Bus,
    stream: str,
    group: str,
    consumer: str,
    handler: Handler,
    stop: asyncio.Event,
    *,
    count: int = 50,
    block_ms: int = 1000,
    start: StartAt = "earliest",
    max_attempts: int = 3,
) -> None:
    """Run `handler` per message; retry, then park poison messages in the dead-letter stream."""
    backoff = 1.0
    while not stop.is_set():
        try:
            messages = await bus.read(
                stream, group, consumer, count=count, block_ms=block_ms, start=start
            )
            backoff = 1.0
        except Exception:
            log.exception("bus.read_failed", stream=stream, group=group)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue
        for message in messages:
            for attempt in range(1, max_attempts + 1):
                try:
                    await handler(message)
                    break
                except Exception as exc:
                    log.exception("bus.handler_failed", stream=stream, group=group, attempt=attempt)
                    if attempt == max_attempts:
                        await bus.publish(
                            STREAM_DEADLETTER,
                            {
                                "stream": stream,
                                "group": group,
                                "message_id": message.id,
                                "error": repr(exc)[:500],
                                "data": message.data,
                            },
                        )
                    else:
                        await asyncio.sleep(0.2 * attempt)
            await bus.ack(stream, group, [message.id])

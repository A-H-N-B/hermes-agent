from __future__ import annotations

import asyncio
import uuid

import pytest

from gold_agent.core.bus import (
    STREAM_DEADLETTER,
    Bus,
    MemoryBus,
    Message,
    RedisBus,
    consume,
)
from tests.conftest import redis_url


async def _bus_contract(bus: Bus) -> None:
    stream = f"test.{uuid.uuid4().hex[:8]}"
    await bus.publish(stream, {"n": 1})
    await bus.publish(stream, {"n": 2})
    a = await bus.read(stream, "analyzer", "a1", block_ms=100)
    b = await bus.read(stream, "notifier", "n1", block_ms=100)
    assert [m.data["n"] for m in a] == [1, 2]
    assert [m.data["n"] for m in b] == [1, 2]  # every group sees every message
    await bus.ack(stream, "analyzer", [m.id for m in a])
    assert await bus.read(stream, "analyzer", "a1", block_ms=50) == []
    await bus.publish(stream, {"n": 3})
    assert [m.data["n"] for m in await bus.read(stream, "analyzer", "a1", block_ms=100)] == [3]


async def test_memory_bus_fans_out_to_each_group() -> None:
    await _bus_contract(MemoryBus())


async def test_memory_bus_blocks_until_a_message_arrives() -> None:
    bus = MemoryBus()

    async def later() -> None:
        await asyncio.sleep(0.05)
        await bus.publish("s", {"x": 1})

    task = asyncio.create_task(later())
    messages = await bus.read("s", "g", "c", block_ms=2000, start="latest")
    await task
    assert [m.data for m in messages] == [{"x": 1}]


async def test_memory_bus_trims_old_messages() -> None:
    bus = MemoryBus(maxlen=3)
    for n in range(5):
        await bus.publish("s", {"n": n})
    assert [m.data["n"] for m in await bus.read("s", "g", "c", block_ms=0)] == [2, 3, 4]


async def test_consume_retries_then_dead_letters_poison_messages() -> None:
    bus = MemoryBus()
    attempts: dict[int, int] = {}
    handled: list[int] = []
    stop = asyncio.Event()

    async def handler(message: Message) -> None:
        n = int(message.data["n"])
        attempts[n] = attempts.get(n, 0) + 1
        if n == 1:
            raise ValueError("poison")
        handled.append(n)
        if n == 2:
            stop.set()

    await bus.publish("s", {"n": 1})
    await bus.publish("s", {"n": 2})
    await asyncio.wait_for(consume(bus, "s", "g", "c", handler, stop, block_ms=50), timeout=5)
    assert attempts[1] == 3 and handled == [2]
    dead = await bus.read(STREAM_DEADLETTER, "ops", "x", block_ms=0)
    assert dead and dead[0].data["data"] == {"n": 1}


@pytest.mark.skipif(redis_url() is None, reason="set GOLD_TEST_REDIS_URL to run Redis tests")
async def test_redis_bus_contract_and_crash_recovery() -> None:
    url = redis_url()
    assert url
    bus = RedisBus(url)
    try:
        await _bus_contract(bus)
        stream = f"test.{uuid.uuid4().hex[:8]}"
        await bus.publish(stream, {"n": 1})
        crashed = await bus.read(stream, "g", "worker-1", block_ms=100)
        assert crashed  # read but never acked: the worker "crashed"
        lag = await bus.lag(stream, "g")
        assert lag == 1
    finally:
        await bus.close()

"""Event bus for SSE fan-out (ARCHITECTURE.md §2, T-18).

The worker publishes step events; the api subscribes and relays them to connected
browsers. A narrow protocol lets Redis pub/sub back it in production and an
in-memory queue back it in tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


def channel_for(investigation_id: UUID) -> str:
    return f"inv:{investigation_id}"


@runtime_checkable
class EventBus(Protocol):
    async def publish(self, investigation_id: UUID, event: dict[str, Any]) -> None: ...

    def subscribe(
        self, investigation_id: UUID
    ) -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]: ...


class InMemoryEventBus:
    """Process-local bus for dev/tests (not shared across workers)."""

    def __init__(self) -> None:
        self._subs: dict[UUID, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def publish(self, investigation_id: UUID, event: dict[str, Any]) -> None:
        for queue in list(self._subs.get(investigation_id, [])):
            await queue.put(event)

    @asynccontextmanager
    async def subscribe(
        self, investigation_id: UUID
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subs.setdefault(investigation_id, []).append(queue)

        async def _drain() -> AsyncIterator[dict[str, Any]]:
            while True:
                yield await queue.get()

        try:
            yield _drain()
        finally:
            self._subs[investigation_id].remove(queue)


class RedisEventBus:
    """Redis pub/sub bus. ``client`` is a ``redis.asyncio.Redis``-compatible object."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def publish(self, investigation_id: UUID, event: dict[str, Any]) -> None:
        await self._client.publish(channel_for(investigation_id), json.dumps(event))

    @asynccontextmanager
    async def subscribe(
        self, investigation_id: UUID
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel_for(investigation_id))

        async def _drain() -> AsyncIterator[dict[str, Any]]:
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    yield json.loads(message["data"])

        try:
            yield _drain()
        finally:
            await pubsub.unsubscribe(channel_for(investigation_id))
            await pubsub.aclose()

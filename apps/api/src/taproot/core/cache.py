"""Small async cache abstraction (ARCHITECTURE.md §8.1 — JWKS cache).

A narrow protocol so the JWKS cache can be backed by Redis in production and by
an in-memory dict in tests, with no other code change.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AsyncCache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class InMemoryCache:
    """Non-expiring in-process cache. Dev/test only — not shared across workers."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._data[key] = value


class RedisCache:
    """Redis-backed cache. ``client`` is a ``redis.asyncio.Redis``-compatible object."""

    def __init__(self, client: object) -> None:
        self._client = client

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)  # type: ignore[attr-defined]
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._client.set(key, value, ex=ttl_seconds)  # type: ignore[attr-defined]

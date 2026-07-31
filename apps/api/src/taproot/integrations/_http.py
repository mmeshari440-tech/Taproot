"""Shared helpers for integration clients."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


async def send_with_retries(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    max_retries: int = 3,
    backoff_base: float = 0.5,
) -> httpx.Response:
    """Invoke ``send`` with jittered exponential backoff, retrying only on
    5xx/429 (ARCHITECTURE.md §3). ``send`` is re-called per attempt."""
    resp = await send()
    for attempt in range(max_retries):
        if resp.status_code not in RETRYABLE_STATUS:
            return resp
        delay = backoff_base * (2**attempt) + random.uniform(0, backoff_base)  # noqa: S311
        if delay:
            await asyncio.sleep(delay)
        resp = await send()
    return resp


def short_error(resp: httpx.Response) -> str:
    """Best-effort extraction of a provider's human-readable error message."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or f"HTTP {resp.status_code}"
    if isinstance(data, dict):
        for key in ("message", "detail", "error_description", "error"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                reason = value.get("reason") or value.get("message")
                if reason:
                    return str(reason)
    return str(data)[:300]

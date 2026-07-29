"""Shared helpers for integration clients."""

from __future__ import annotations

import httpx


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

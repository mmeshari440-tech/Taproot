"""Elasticsearch integration — connection test (T-11).

The full typed client (`search`/`thread`/`histogram`/`cardinality`) lands in
Sprint 2 (T-13). This module currently implements the connection test used by the
admin integration flow: ``_search size:0`` against the configured index pattern
(PLAN.md §5.1).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from taproot.core.models import ConnectionTestResult
from taproot.integrations._http import short_error


async def test_connection(
    *,
    base_url: str,
    external_id: str | None,
    config: dict[str, Any],
    token: str | None,
    http_client: httpx.AsyncClient,
) -> ConnectionTestResult:
    """`external_id` is the index pattern (e.g. ``logs-*``)."""
    index = external_id or "*"
    url = f"{base_url.rstrip('/')}/{index}/_search"
    headers = {"Authorization": f"ApiKey {token}"} if token else {}
    start = time.perf_counter()
    try:
        resp = await http_client.post(
            url, params={"size": 0}, json={"query": {"match_all": {}}}, headers=headers
        )
    except httpx.HTTPError as exc:
        return ConnectionTestResult(ok=False, latency_ms=0, error=str(exc))
    latency = int((time.perf_counter() - start) * 1000)
    if resp.status_code == 200:
        hits = resp.json().get("hits", {}).get("total", {})
        return ConnectionTestResult(
            ok=True, latency_ms=latency, detail=f"index '{index}' reachable ({hits})"
        )
    return ConnectionTestResult(ok=False, latency_ms=latency, error=short_error(resp))

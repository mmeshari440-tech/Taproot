"""Sentry integration — connection test (T-11).

Full issue/event client lands in Sprint 2 (T-14). Connection test hits
``GET /api/0/projects/{org}/{project}/`` (PLAN.md §5.1).
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
    """`external_id` is ``"<org>/<project>"`` (or ``org``/``project`` in config)."""
    org = config.get("org")
    project = config.get("project")
    if (not org or not project) and external_id and "/" in external_id:
        org, project = external_id.split("/", 1)
    if not org or not project:
        return ConnectionTestResult(
            ok=False, latency_ms=0, error="Sentry needs external_id as '<org>/<project>'"
        )

    url = f"{base_url.rstrip('/')}/api/0/projects/{org}/{project}/"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    start = time.perf_counter()
    try:
        resp = await http_client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return ConnectionTestResult(ok=False, latency_ms=0, error=str(exc))
    latency = int((time.perf_counter() - start) * 1000)
    if resp.status_code == 200:
        return ConnectionTestResult(
            ok=True, latency_ms=latency, detail=f"project '{org}/{project}' reachable"
        )
    return ConnectionTestResult(ok=False, latency_ms=latency, error=short_error(resp))

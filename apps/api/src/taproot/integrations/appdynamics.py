"""AppDynamics integration — connection test (T-11).

Full BT/snapshot client lands in Sprint 2 (T-15). Connection test performs the
documented OAuth client-credentials flow then reads the application
(PLAN.md §5.1).

# TODO: verify against a live AppDynamics controller (OAuth path, app id form).
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
    """`external_id` is the application id; `config.client_id` + `token` (secret)
    are the OAuth API client credentials."""
    base = base_url.rstrip("/")
    client_id = config.get("client_id")
    if not client_id or not token:
        return ConnectionTestResult(
            ok=False, latency_ms=0, error="AppDynamics needs config.client_id and a client secret"
        )

    start = time.perf_counter()
    try:
        token_resp = await http_client.post(
            f"{base}/controller/api/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": token,
            },
        )
        if token_resp.status_code != 200:
            return ConnectionTestResult(
                ok=False,
                latency_ms=int((time.perf_counter() - start) * 1000),
                error=short_error(token_resp),
            )
        access = token_resp.json().get("access_token")
        app_resp = await http_client.get(
            f"{base}/controller/rest/applications/{external_id}",
            params={"output": "JSON"},
            headers={"Authorization": f"Bearer {access}"},
        )
    except httpx.HTTPError as exc:
        return ConnectionTestResult(ok=False, latency_ms=0, error=str(exc))

    latency = int((time.perf_counter() - start) * 1000)
    if app_resp.status_code == 200:
        return ConnectionTestResult(
            ok=True, latency_ms=latency, detail=f"application '{external_id}' reachable"
        )
    return ConnectionTestResult(ok=False, latency_ms=latency, error=short_error(app_resp))

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

from taproot.core.logging import get_logger
from taproot.core.models import (
    AppDErrorSnapshot,
    BusinessTransaction,
    ConnectionTestResult,
    ExitCall,
)
from taproot.integrations._http import send_with_retries, short_error

_log = get_logger(__name__)


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


class AppDynamicsClient:
    """Read-only AppDynamics client (T-15) with OAuth token caching.

    # TODO: verify against a live controller (snapshot endpoint shape, metric paths).
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        app_id: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._app = app_id
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._token: str | None = None
        self._token_expiry: float = 0.0

    async def _access_token(self) -> str:
        # Refresh ~30s before expiry.
        if self._token and time.monotonic() < self._token_expiry - 30:
            return self._token
        resp = await self._http.post(
            f"{self._base}/controller/api/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = str(payload["access_token"])
        self._token_expiry = time.monotonic() + float(payload.get("expires_in", 300))
        return self._token

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        token = await self._access_token()
        merged = {"output": "JSON", **(params or {})}
        start = time.perf_counter()
        resp = await send_with_retries(
            lambda: self._http.get(
                f"{self._base}{path}", params=merged, headers={"Authorization": f"Bearer {token}"}
            ),
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
        )
        _log.info(
            "appd_call",
            provider="appdynamics",
            method=f"GET {path}",
            status=resp.status_code,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        resp.raise_for_status()
        return resp

    async def business_transactions(self) -> list[BusinessTransaction]:
        resp = await self._get(f"/controller/rest/applications/{self._app}/business-transactions")
        return [
            BusinessTransaction(
                id=int(bt["id"]), name=bt.get("name", ""), tier_name=bt.get("tierName")
            )
            for bt in resp.json()
        ]

    async def error_snapshots(self, *, duration_mins: int = 60) -> list[AppDErrorSnapshot]:
        resp = await self._get(
            f"/controller/rest/applications/{self._app}/request-snapshots",
            {
                "time-range-type": "BEFORE_NOW",
                "duration-in-mins": duration_mins,
                "error-occurred": "true",
            },
        )
        snapshots: list[AppDErrorSnapshot] = []
        for snap in resp.json():
            exit_calls = [
                ExitCall(
                    target=ec.get("target", ""),
                    call_type=ec.get("exitPointType"),
                    error_count=int(ec.get("errorCount", 0)),
                )
                for ec in snap.get("exitCalls", [])
            ]
            snapshots.append(
                AppDErrorSnapshot(
                    id=str(snap.get("requestGUID") or snap.get("id") or "") or None,
                    error_message=snap.get("errorDetails"),
                    exit_calls=exit_calls,
                )
            )
        return snapshots

    async def metric_data(self, metric_path: str, *, duration_mins: int = 60) -> list[float]:
        resp = await self._get(
            f"/controller/rest/applications/{self._app}/metric-data",
            {
                "metric-path": metric_path,
                "time-range-type": "BEFORE_NOW",
                "duration-in-mins": duration_mins,
                "rollup": "false",
            },
        )
        values: list[float] = []
        for metric in resp.json():
            for point in metric.get("metricValues", []):
                values.append(float(point.get("value", 0)))
        return values

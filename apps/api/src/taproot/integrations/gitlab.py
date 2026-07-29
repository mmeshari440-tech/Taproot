"""GitLab client (T-09) — group discovery, repo listing, file fetch.

Per the integration client contract (ARCHITECTURE.md §3): read-only, returns
normalized models, 30s timeout, retries on 5xx/429 only with jittered backoff,
structured per-call logging. The platform-level token needs **read_api** scope
only.
"""

from __future__ import annotations

import asyncio
import base64
import random
import time
from typing import Any
from urllib.parse import quote

import httpx

from taproot.core.exceptions import IntegrationError, NotFoundError
from taproot.core.logging import get_logger
from taproot.core.models import (
    ConnectionTestResult,
    GitLabFile,
    GitLabGroup,
    GitLabProject,
)

_log = get_logger(__name__)

_RETRYABLE = {429, 500, 502, 503, 504}


class GitLabClient:
    """Async GitLab REST v4 client.

    ``http_client`` is injectable so the client is testable without network. The
    ``sleep`` hook lets tests disable backoff delays.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    @property
    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self._token}

    async def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        url = f"{self._base}/api/v4/{path.lstrip('/')}"
        start = time.perf_counter()
        resp: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            resp = await self._http.request(method, url, params=params, headers=self._headers)
            if resp.status_code in _RETRYABLE and attempt < self._max_retries:
                # Jittered exponential backoff; retry only on 5xx/429 (§3).
                delay = self._backoff_base * (2**attempt) + random.uniform(0, self._backoff_base)  # noqa: S311
                if delay:
                    await asyncio.sleep(delay)
                continue
            break
        assert resp is not None  # noqa: S101  (loop runs at least once)
        _log.info(
            "gitlab_call",
            provider="gitlab",
            method=f"{method} {path}",
            status=resp.status_code,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        return resp

    @staticmethod
    def _raise_for_status(resp: httpx.Response, *, context: str) -> None:
        if resp.status_code == 404:
            raise NotFoundError(f"GitLab: {context} not found")
        if resp.status_code >= 400:
            raise IntegrationError(f"GitLab {resp.status_code}: {_message(resp)}")

    async def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            resp = await self._request("GET", "version")
        except httpx.HTTPError as exc:
            return ConnectionTestResult(ok=False, latency_ms=0, error=str(exc))
        latency = int((time.perf_counter() - start) * 1000)
        if resp.status_code == 200:
            return ConnectionTestResult(
                ok=True, latency_ms=latency, detail=resp.json().get("version")
            )
        return ConnectionTestResult(ok=False, latency_ms=latency, error=_message(resp))

    async def list_groups(self, search: str | None = None) -> list[GitLabGroup]:
        params: dict[str, Any] = {"per_page": 50, "order_by": "path", "sort": "asc"}
        if search:
            params["search"] = search
        resp = await self._request("GET", "groups", params)
        self._raise_for_status(resp, context="groups")
        return [
            GitLabGroup(
                id=g["id"], name=g["name"], full_path=g["full_path"], web_url=g.get("web_url")
            )
            for g in resp.json()
        ]

    async def list_group_projects(self, group_id: int) -> list[GitLabProject]:
        params = {"include_subgroups": "true", "per_page": 100, "archived": "false"}
        resp = await self._request("GET", f"groups/{group_id}/projects", params)
        self._raise_for_status(resp, context=f"group {group_id}")
        return [
            GitLabProject(
                id=p["id"],
                name=p["name"],
                path_with_namespace=p["path_with_namespace"],
                default_branch=p.get("default_branch") or "main",
                web_url=p.get("web_url"),
                topics=p.get("topics", []),
            )
            for p in resp.json()
        ]

    async def get_file(self, project_id: int, path: str, ref: str) -> GitLabFile:
        encoded = quote(path, safe="")
        resp = await self._request(
            "GET", f"projects/{project_id}/repository/files/{encoded}", {"ref": ref}
        )
        self._raise_for_status(resp, context=f"{path}@{ref}")
        payload = resp.json()
        content = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
        return GitLabFile(path=path, ref=ref, content=content)


def _message(resp: httpx.Response) -> str:
    """Extract GitLab's human-readable error message, falling back to the body."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:500]
    if isinstance(data, dict):
        msg = data.get("message") or data.get("error")
        if msg:
            return str(msg)
    return str(data)[:500]

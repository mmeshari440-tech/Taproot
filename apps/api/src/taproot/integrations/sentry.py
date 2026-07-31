"""Sentry integration — connection test (T-11) + client (T-14).

`SentryClient` implements `search_issues`/`latest_event`/`issue_tags`, normalizing
stack frames to the shared `Frame` model and extracting the release SHA (feeds
code_locate, T-25). `# TODO: verify against a live Sentry instance.`
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from taproot.core.logging import get_logger
from taproot.core.models import (
    ConnectionTestResult,
    Frame,
    SentryEventDetail,
    SentryIssue,
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


def _parse_frames(event: dict[str, Any]) -> list[Frame]:
    """Pull frames from a Sentry event's exception entry, normalized to Frame."""
    frames: list[Frame] = []
    for entry in event.get("entries", []):
        if entry.get("type") != "exception":
            continue
        for value in entry.get("data", {}).get("values", []):
            for f in value.get("stacktrace", {}).get("frames", []):
                frames.append(
                    Frame(
                        filename=f.get("filename"),
                        function=f.get("function"),
                        lineno=f.get("lineNo") or f.get("lineno"),
                        module=f.get("module"),
                        abs_path=f.get("absPath"),
                        in_app=bool(f.get("inApp", False)),
                    )
                )
    return frames


class SentryClient:
    """Read-only Sentry client (T-14)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        org: str,
        project: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._org = org
        self._project = project
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {token}"}
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        url = f"{self._base}{path}"
        start = time.perf_counter()
        resp = await send_with_retries(
            lambda: self._http.get(url, params=params, headers=self._headers),
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
        )
        _log.info(
            "sentry_call",
            provider="sentry",
            method=f"GET {path}",
            status=resp.status_code,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        resp.raise_for_status()
        return resp

    async def search_issues(self, query: str, *, limit: int = 10) -> list[SentryIssue]:
        resp = await self._get(
            f"/api/0/projects/{self._org}/{self._project}/issues/",
            {"query": query, "limit": limit},
        )
        return [
            SentryIssue(
                id=str(i["id"]),
                title=i.get("title", ""),
                culprit=i.get("culprit"),
                level=i.get("level"),
                count=int(i["count"]) if str(i.get("count", "")).isdigit() else None,
                user_count=i.get("userCount"),
                first_seen=i.get("firstSeen"),
                last_seen=i.get("lastSeen"),
                permalink=i.get("permalink"),
            )
            for i in resp.json()
        ]

    async def latest_event(self, issue_id: str) -> SentryEventDetail:
        resp = await self._get(f"/api/0/issues/{issue_id}/events/latest/")
        event = resp.json()
        tags = {t.get("key"): t.get("value") for t in event.get("tags", []) if t.get("key")}
        return SentryEventDetail(
            event_id=str(event.get("id") or event.get("eventID") or ""),
            release=(event.get("release") or {}).get("version")
            if isinstance(event.get("release"), dict)
            else event.get("release"),
            frames=_parse_frames(event),
            tags=tags,
        )

    async def issue_tags(self, issue_id: str) -> dict[str, list[str]]:
        resp = await self._get(f"/api/0/issues/{issue_id}/tags/")
        return {
            t["key"]: [v.get("value", "") for v in t.get("topValues", [])]
            for t in resp.json()
            if t.get("key")
        }

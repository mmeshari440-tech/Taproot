from __future__ import annotations

import base64

import httpx
import pytest

from taproot.core.exceptions import IntegrationError, NotFoundError
from taproot.integrations.gitlab import GitLabClient


def _client(handler: object, **kw: object) -> GitLabClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport, base_url="https://gl.test")
    return GitLabClient("https://gl.test", "glpat-x", http_client=http, backoff_base=0.0, **kw)  # type: ignore[arg-type]


async def test_list_groups_normalizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v4/groups"
        assert request.headers["PRIVATE-TOKEN"] == "glpat-x"
        return httpx.Response(
            200,
            json=[{"id": 1, "name": "Payments", "full_path": "acme/payments", "web_url": "u"}],
        )

    groups = await _client(handler).list_groups("pay")
    assert groups[0].id == 1
    assert groups[0].full_path == "acme/payments"


async def test_list_group_projects_normalizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 10,
                    "name": "be-payments",
                    "path_with_namespace": "acme/payments/be-payments",
                    "default_branch": "main",
                    "topics": ["backend"],
                }
            ],
        )

    projects = await _client(handler).list_group_projects(1)
    assert projects[0].id == 10
    assert projects[0].topics == ["backend"]


async def test_get_file_decodes_base64() -> None:
    content = "line1\nline2\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert "repository/files" in request.url.path
        assert request.url.params["ref"] == "abc123"
        return httpx.Response(200, json={"content": base64.b64encode(content.encode()).decode()})

    f = await _client(handler).get_file(10, "src/app.py", "abc123")
    assert f.content == content
    assert f.ref == "abc123"


async def test_get_file_missing_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "404 File Not Found"})

    with pytest.raises(NotFoundError):
        await _client(handler).get_file(10, "nope.py", "main")


async def test_error_surfaces_provider_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "insufficient_scope"})

    with pytest.raises(IntegrationError, match="insufficient_scope"):
        await _client(handler).list_groups()


async def test_retries_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"message": "try later"})
        return httpx.Response(200, json=[])

    groups = await _client(handler).list_groups()
    assert groups == []
    assert calls["n"] == 3  # 2 retries then success


async def test_test_connection_ok_and_fail() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "17.0"})

    result = await _client(ok_handler).test_connection()
    assert result.ok and result.detail == "17.0"

    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "401 Unauthorized"})

    result = await _client(fail_handler).test_connection()
    assert not result.ok
    assert "Unauthorized" in (result.error or "")

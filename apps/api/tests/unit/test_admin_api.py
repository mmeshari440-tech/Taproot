from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from taproot.api.v1.deps import (
    get_gitlab_client,
    get_http_client,
    get_secret_store,
    get_token_validator,
)
from taproot.core.models import GitLabGroup, GitLabProject
from taproot.core.secrets import LocalEncryptedSecretStore
from taproot.core.security import Principal
from taproot.db.base import Base
from taproot.db.session import get_session
from taproot.main import create_app


class _StubValidator:
    async def validate(self, token: str) -> Principal:
        roles = ["platform-admin"] if token == "admin" else ["tech-user"]
        return Principal(sub=token, email=f"{token}@t.co", name=token, roles=roles)


class _FakeGitLab:
    async def list_groups(self, search: str | None = None) -> list[GitLabGroup]:
        return [GitLabGroup(id=7, name="Payments", full_path="acme/payments")]

    async def list_group_projects(self, group_id: int) -> list[GitLabProject]:
        return [
            GitLabProject(id=1, name="be-payments", path_with_namespace="a/be", topics=[]),
            GitLabProject(id=2, name="web-portal", path_with_namespace="a/web", topics=[]),
        ]


def _mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://es.test")


@pytest_asyncio.fixture
async def env() -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session() -> AsyncIterator[Any]:
        async with maker() as s:
            yield s

    store = LocalEncryptedSecretStore(key="test-key")
    app = create_app()
    app.dependency_overrides[get_token_validator] = lambda: _StubValidator()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_secret_store] = lambda: store
    app.dependency_overrides[get_gitlab_client] = lambda: _FakeGitLab()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield app, c
    await engine.dispose()


ADMIN = {"Authorization": "Bearer admin"}
TECH = {"Authorization": "Bearer tech"}


async def _make_project(c: AsyncClient, **body: Any) -> str:
    payload = {"name": "Payments", "gitlab_group_id": 7, **body}
    resp = await c.post("/api/v1/projects", json=payload, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- T-10 project CRUD ------------------------------------------------------
async def test_non_admin_cannot_create_project(env: tuple[FastAPI, AsyncClient]) -> None:
    _, c = env
    resp = await c.post("/api/v1/projects", json={"name": "X"}, headers=TECH)
    assert resp.status_code == 403


async def test_admin_creates_and_lists_project(env: tuple[FastAPI, AsyncClient]) -> None:
    _, c = env
    pid = await _make_project(c)
    listed = await c.get("/api/v1/projects", headers=TECH)  # any authed user may read
    assert listed.status_code == 200
    assert any(p["id"] == pid for p in listed.json())


async def test_gitlab_groups_discovery(env: tuple[FastAPI, AsyncClient]) -> None:
    _, c = env
    resp = await c.get("/api/v1/projects/gitlab-groups", params={"search": "pay"}, headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json()[0]["full_path"] == "acme/payments"


async def test_sync_repos_and_override_kind(env: tuple[FastAPI, AsyncClient]) -> None:
    _, c = env
    pid = await _make_project(c)
    synced = await c.post(f"/api/v1/projects/{pid}/sync-repos", headers=ADMIN)
    assert synced.status_code == 200
    kinds = {r["name"]: r["kind"] for r in synced.json()}
    assert kinds == {"be-payments": "BE", "web-portal": "FE"}

    repos = (await c.get(f"/api/v1/projects/{pid}/repos", headers=TECH)).json()
    be = next(r for r in repos if r["name"] == "be-payments")
    patched = await c.patch(
        f"/api/v1/projects/{pid}/repos/{be['id']}",
        json={"kind": "OTHER", "org_package_prefixes": ["com.acme."]},
        headers=ADMIN,
    )
    assert patched.status_code == 200
    assert patched.json()["kind"] == "OTHER"
    assert patched.json()["org_package_prefixes"] == ["com.acme."]


# --- T-11 integrations (per-repo, ADR-0002) --------------------------------
async def _first_repo(c: AsyncClient, pid: str) -> str:
    await c.post(f"/api/v1/projects/{pid}/sync-repos", headers=ADMIN)
    repos = (await c.get(f"/api/v1/projects/{pid}/repos", headers=ADMIN)).json()
    return str(repos[0]["id"])


async def test_put_integration_never_returns_token(env: tuple[FastAPI, AsyncClient]) -> None:
    _, c = env
    pid = await _make_project(c)
    rid = await _first_repo(c, pid)
    resp = await c.put(
        f"/api/v1/projects/{pid}/repos/{rid}/integrations/ELASTIC",
        json={"external_id": "logs-*", "base_url": "https://es.test", "token": "sekret-apikey"},
        headers=ADMIN,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "UNVERIFIED"
    assert body["has_secret"] is True
    assert "token" not in body
    assert "sekret-apikey" not in resp.text


async def test_non_admin_cannot_configure_integration(env: tuple[FastAPI, AsyncClient]) -> None:
    _, c = env
    pid = await _make_project(c)
    rid = await _first_repo(c, pid)
    resp = await c.put(
        f"/api/v1/projects/{pid}/repos/{rid}/integrations/ELASTIC",
        json={"external_id": "logs-*", "base_url": "https://es.test", "token": "x"},
        headers=TECH,
    )
    assert resp.status_code == 403


async def test_integration_test_success_sets_status_ok(env: tuple[FastAPI, AsyncClient]) -> None:
    app, c = env
    pid = await _make_project(c)
    rid = await _first_repo(c, pid)
    base = f"/api/v1/projects/{pid}/repos/{rid}/integrations"
    await c.put(
        f"{base}/ELASTIC",
        json={"external_id": "logs-*", "base_url": "https://es.test", "token": "k"},
        headers=ADMIN,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/_search")
        return httpx.Response(200, json={"hits": {"total": {"value": 0}}})

    app.dependency_overrides[get_http_client] = lambda: _mock_http(handler)
    resp = await c.post(f"{base}/ELASTIC/test", headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    integrations = (await c.get(base, headers=TECH)).json()
    assert integrations[0]["status"] == "OK"


async def test_integration_test_failure_returns_422_with_provider_message(
    env: tuple[FastAPI, AsyncClient],
) -> None:
    app, c = env
    pid = await _make_project(c)
    rid = await _first_repo(c, pid)
    base = f"/api/v1/projects/{pid}/repos/{rid}/integrations"
    await c.put(
        f"{base}/ELASTIC",
        json={"external_id": "logs-*", "base_url": "https://es.test", "token": "bad"},
        headers=ADMIN,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"reason": "invalid apikey"}})

    app.dependency_overrides[get_http_client] = lambda: _mock_http(handler)
    resp = await c.post(f"{base}/ELASTIC/test", headers=ADMIN)
    assert resp.status_code == 422
    assert "invalid apikey" in resp.text

    integrations = (await c.get(base, headers=ADMIN)).json()
    assert integrations[0]["status"] == "FAILED"
    assert "invalid apikey" in integrations[0]["last_error"]

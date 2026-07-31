from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from taproot.api.v1.deps import get_job_queue, get_token_validator
from taproot.core.security import Principal
from taproot.db.base import Base
from taproot.db.models import Integration, IntegrationKind, IntegrationStatus, Project
from taproot.db.session import get_session
from taproot.main import create_app
from taproot.workers.queue import FakeJobQueue


class _StubValidator:
    async def validate(self, token: str) -> Principal:
        roles = ["platform-admin"] if token == "admin" else ["tech-user"]
        return Principal(sub=token, email=f"{token}@t.co", name=token, roles=roles)


class _Env:
    def __init__(self, client: AsyncClient, maker: async_sessionmaker[Any], queue: FakeJobQueue):
        self.client = client
        self.maker = maker
        self.queue = queue

    async def seed_project(self, *, elastic: IntegrationStatus | None) -> UUID:
        async with self.maker() as s:
            project = Project(name="Payments", slug=f"p-{len(self.queue.enqueued)}-{id(s)}")
            s.add(project)
            await s.flush()
            if elastic is not None:
                s.add(
                    Integration(project_id=project.id, kind=IntegrationKind.ELASTIC, status=elastic)
                )
            await s.commit()
            return project.id


@pytest_asyncio.fixture
async def env() -> AsyncIterator[_Env]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    queue = FakeJobQueue()

    async def _session() -> AsyncIterator[Any]:
        async with maker() as s:
            yield s

    app: FastAPI = create_app()
    app.dependency_overrides[get_token_validator] = lambda: _StubValidator()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_job_queue] = lambda: queue

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield _Env(c, maker, queue)
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_submit_enqueues_when_elastic_ok(env: _Env) -> None:
    pid = await env.seed_project(elastic=IntegrationStatus.OK)
    resp = await env.client.post(
        "/api/v1/investigations",
        json={"project_id": str(pid), "error_text": "NullPointer"},
        headers=_auth("alice"),
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "QUEUED"
    assert len(env.queue.enqueued) == 1


async def test_submit_rejected_when_elastic_not_ok(env: _Env) -> None:
    pid = await env.seed_project(elastic=IntegrationStatus.FAILED)
    resp = await env.client.post(
        "/api/v1/investigations",
        json={"project_id": str(pid), "error_text": "NullPointer"},
        headers=_auth("alice"),
    )
    assert resp.status_code == 422
    assert "Elasticsearch" in resp.json()["detail"]
    assert env.queue.enqueued == []


async def test_list_is_scoped_to_caller(env: _Env) -> None:
    pid = await env.seed_project(elastic=IntegrationStatus.OK)
    body = {"project_id": str(pid), "error_text": "x"}
    await env.client.post("/api/v1/investigations", json=body, headers=_auth("alice"))
    await env.client.post("/api/v1/investigations", json=body, headers=_auth("alice"))
    await env.client.post("/api/v1/investigations", json=body, headers=_auth("bob"))

    alice_list = (await env.client.get("/api/v1/investigations", headers=_auth("alice"))).json()
    bob_list = (await env.client.get("/api/v1/investigations", headers=_auth("bob"))).json()
    assert alice_list["total"] == 2
    assert bob_list["total"] == 1


async def test_get_foreign_investigation_forbidden(env: _Env) -> None:
    pid = await env.seed_project(elastic=IntegrationStatus.OK)
    created = await env.client.post(
        "/api/v1/investigations",
        json={"project_id": str(pid), "error_text": "x"},
        headers=_auth("alice"),
    )
    inv_id = created.json()["id"]

    forbidden = await env.client.get(f"/api/v1/investigations/{inv_id}", headers=_auth("bob"))
    assert forbidden.status_code == 403
    admin_ok = await env.client.get(f"/api/v1/investigations/{inv_id}", headers=_auth("admin"))
    assert admin_ok.status_code == 200


async def test_cancel_own_investigation(env: _Env) -> None:
    pid = await env.seed_project(elastic=IntegrationStatus.OK)
    inv_id = (
        await env.client.post(
            "/api/v1/investigations",
            json={"project_id": str(pid), "error_text": "x"},
            headers=_auth("alice"),
        )
    ).json()["id"]

    resp = await env.client.post(f"/api/v1/investigations/{inv_id}/cancel", headers=_auth("alice"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"

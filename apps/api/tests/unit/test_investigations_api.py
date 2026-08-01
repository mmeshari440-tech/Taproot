from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from taproot.api.v1.deps import get_event_bus, get_job_queue, get_token_validator
from taproot.core.events import InMemoryEventBus
from taproot.core.security import Principal
from taproot.db.base import Base
from taproot.db.models import (
    Integration,
    IntegrationKind,
    IntegrationStatus,
    Investigation,
    InvestigationStatus,
    InvestigationStep,
    Project,
    ProjectRepo,
    StepStatus,
)
from taproot.db.session import get_session
from taproot.main import create_app
from taproot.workers.queue import FakeJobQueue


class _StubValidator:
    async def validate(self, token: str) -> Principal:
        if token in ("", "bad"):
            from taproot.core.exceptions import AuthenticationError

            raise AuthenticationError("invalid token")
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
            repo = ProjectRepo(project_id=project.id, gitlab_project_id=1, name="app")
            s.add(repo)
            await s.flush()
            if elastic is not None:
                s.add(
                    Integration(
                        project_repo_id=repo.id, kind=IntegrationKind.ELASTIC, status=elastic
                    )
                )
            await s.commit()
            return project.id

    async def seed_done_investigation(self, *, owner: str, steps: int = 2) -> UUID:
        """Insert a terminal investigation with persisted steps (for SSE replay)."""
        async with self.maker() as s:
            project = Project(name="P", slug=f"done-{id(s)}")
            s.add(project)
            await s.flush()
            # created_by must match the upserted user id — resolved after first auth.
            inv = Investigation(
                project_id=project.id,
                error_text="boom",
                status=InvestigationStatus.DONE,
                created_by=None,
            )
            s.add(inv)
            await s.flush()
            for i in range(1, steps + 1):
                s.add(
                    InvestigationStep(
                        investigation_id=inv.id,
                        seq=i,
                        node=f"node{i}",
                        title=f"step {i}",
                        status=StepStatus.ok,
                        summary=f"ok {i}",
                    )
                )
            await s.commit()
            return inv.id

    async def set_owner(self, investigation_id: UUID, sub: str) -> None:
        """Point an investigation at the user id that `sub` upserts to."""
        from taproot.db.models import User

        async with self.maker() as s:
            user = (
                await s.execute(select(User).where(User.keycloak_sub == sub))
            ).scalar_one()
            inv = await s.get(Investigation, investigation_id)
            assert inv is not None
            inv.created_by = user.id
            await s.commit()


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
    app.dependency_overrides[get_event_bus] = lambda: InMemoryEventBus()

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


# --- T-18 SSE ---------------------------------------------------------------
async def test_stream_rejects_invalid_token(env: _Env) -> None:
    inv_id = await env.seed_done_investigation(owner="alice")
    bad = await env.client.get(f"/api/v1/investigations/{inv_id}/stream?access_token=bad")
    assert bad.status_code == 401
    missing = await env.client.get(f"/api/v1/investigations/{inv_id}/stream")
    assert missing.status_code == 422  # required query param


async def test_stream_foreign_user_forbidden(env: _Env) -> None:
    inv_id = await env.seed_done_investigation(owner="alice")
    await env.client.get("/api/v1/investigations", headers=_auth("alice"))
    await env.client.get("/api/v1/investigations", headers=_auth("bob"))
    await env.set_owner(inv_id, "alice")

    resp = await env.client.get(f"/api/v1/investigations/{inv_id}/stream?access_token=bob")
    assert resp.status_code == 403


async def test_stream_replays_persisted_steps_for_terminal_run(env: _Env) -> None:
    inv_id = await env.seed_done_investigation(owner="alice", steps=2)
    await env.client.get("/api/v1/investigations", headers=_auth("alice"))  # upsert alice
    await env.set_owner(inv_id, "alice")

    resp = await env.client.get(f"/api/v1/investigations/{inv_id}/stream?access_token=alice")
    assert resp.status_code == 200
    body = resp.text
    assert body.count("step.finish") == 2
    assert "node1" in body and "node2" in body
    assert '"type": "done"' in body
    assert "id: 1" in body and "id: 2" in body

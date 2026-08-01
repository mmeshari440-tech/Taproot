"""Integration configuration + connection testing (T-11).

Tokens go to the :class:`SecretStore`; only a ``secret_ref`` is persisted and no
token is ever returned. Connection failures surface the provider's real message
(PLAN.md §5.1) and persist ``status=FAILED`` + ``last_error``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.exceptions import NotFoundError, SecretStoreError
from taproot.core.models import ConnectionTestResult
from taproot.core.secrets import SecretStore
from taproot.db.models import (
    AuditLog,
    Integration,
    IntegrationKind,
    IntegrationStatus,
    ProjectRepo,
)
from taproot.integrations import appdynamics, elastic, sentry

Tester = Callable[..., Awaitable[ConnectionTestResult]]

_TESTERS: dict[IntegrationKind, Tester] = {
    IntegrationKind.ELASTIC: elastic.test_connection,
    IntegrationKind.SENTRY: sentry.test_connection,
    IntegrationKind.APPDYNAMICS: appdynamics.test_connection,
}


async def _get(
    session: AsyncSession, project_repo_id: UUID, kind: IntegrationKind
) -> Integration | None:
    return (
        await session.execute(
            select(Integration).where(
                Integration.project_repo_id == project_repo_id, Integration.kind == kind
            )
        )
    ).scalar_one_or_none()


async def upsert_integration(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    project_repo_id: UUID,
    kind: IntegrationKind,
    external_id: str | None,
    base_url: str | None,
    config: dict[str, Any],
    token: str | None,
    secret_store: SecretStore,
) -> Integration:
    """Create/update integration config. A new ``token`` is stored via the
    SecretStore; when omitted the existing secret is kept (masked-field edits)."""
    integ = await _get(session, project_repo_id, kind)
    secret_ref = integ.secret_ref if integ else None

    if token:
        new_ref = await secret_store.store(f"{project_repo_id}:{kind.value}", token)
        if secret_ref:
            try:
                await secret_store.delete(secret_ref)
            except SecretStoreError:
                pass  # old secret already gone — not fatal
        secret_ref = new_ref

    if integ is None:
        integ = Integration(project_repo_id=project_repo_id, kind=kind)
        session.add(integ)

    integ.external_id = external_id
    integ.base_url = base_url
    integ.config = config
    integ.secret_ref = secret_ref
    integ.status = IntegrationStatus.UNVERIFIED
    integ.last_error = None

    await session.flush()
    session.add(
        AuditLog(
            actor_id=actor_id,
            action="integration.upsert",
            entity_type="integration",
            entity_id=str(integ.id),
            meta={"kind": kind.value},
        )
    )
    return integ


async def test_integration(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    project_repo_id: UUID,
    kind: IntegrationKind,
    secret_store: SecretStore,
    http_client: httpx.AsyncClient,
) -> ConnectionTestResult:
    integ = await _get(session, project_repo_id, kind)
    if integ is None:
        raise NotFoundError(f"No {kind.value} integration for repo {project_repo_id}")

    token = await secret_store.retrieve(integ.secret_ref) if integ.secret_ref else None
    tester = _TESTERS[kind]
    result = await tester(
        base_url=integ.base_url or "",
        external_id=integ.external_id,
        config=integ.config,
        token=token,
        http_client=http_client,
    )

    integ.status = IntegrationStatus.OK if result.ok else IntegrationStatus.FAILED
    integ.last_checked_at = datetime.now(UTC)
    integ.last_error = None if result.ok else result.error
    await session.flush()
    session.add(
        AuditLog(
            actor_id=actor_id,
            action="integration.test",
            entity_type="integration",
            entity_id=str(integ.id),
            meta={"kind": kind.value, "ok": result.ok},
        )
    )
    return result


async def elastic_clients_for_project(
    session: AsyncSession,
    project_id: UUID,
    *,
    secret_store: SecretStore,
    http_client: httpx.AsyncClient,
) -> list[elastic.ElasticClient]:
    """Build a read-only Elastic client per verified app (ADR-0002: one index
    per app). The agent's ``thread_walk`` reasons over all of them; unverified or
    unconfigured apps are simply absent from the list."""
    rows = (
        (
            await session.execute(
                select(Integration)
                .join(ProjectRepo, Integration.project_repo_id == ProjectRepo.id)
                .where(
                    ProjectRepo.project_id == project_id,
                    Integration.kind == IntegrationKind.ELASTIC,
                    Integration.status == IntegrationStatus.OK,
                )
            )
        )
        .scalars()
        .all()
    )
    clients: list[elastic.ElasticClient] = []
    for integ in rows:
        token = await secret_store.retrieve(integ.secret_ref) if integ.secret_ref else None
        clients.append(
            elastic.ElasticClient(
                integ.base_url or "",
                token,
                integ.external_id or "*",
                http_client=http_client,
            )
        )
    return clients


async def list_integrations(session: AsyncSession, project_repo_id: UUID) -> list[Integration]:
    return list(
        (
            await session.execute(
                select(Integration).where(Integration.project_repo_id == project_repo_id)
            )
        )
        .scalars()
        .all()
    )

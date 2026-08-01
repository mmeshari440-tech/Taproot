"""Integration config + connection-test endpoints (T-11, reqs 2/4/5).

Tokens are write-only: accepted on ``PUT``, stored via the SecretStore, and never
returned. A failed test returns **422** carrying the provider's real error and
persists ``status=FAILED`` (PLAN.md §5.1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.api.v1.deps import (
    get_current_admin,
    get_current_user,
    get_http_client,
    get_secret_store,
)
from taproot.core.exceptions import NotFoundError
from taproot.core.secrets import SecretStore
from taproot.db.models import Integration, IntegrationKind, IntegrationStatus, User
from taproot.db.session import get_session
from taproot.services import integration_service

# Per-application: integrations live under a repo (ADR-0002).
router = APIRouter(
    prefix="/projects/{project_id}/repos/{repo_id}/integrations", tags=["integrations"]
)

_TESTABLE = {IntegrationKind.ELASTIC, IntegrationKind.SENTRY, IntegrationKind.APPDYNAMICS}


class IntegrationUpsert(BaseModel):
    external_id: str | None = None
    base_url: str | None = None
    token: str | None = None  # write-only; never echoed back
    config: dict[str, Any] = {}


class IntegrationOut(BaseModel):
    id: UUID
    kind: IntegrationKind
    external_id: str | None
    base_url: str | None
    status: IntegrationStatus
    last_checked_at: datetime | None
    last_error: str | None
    has_secret: bool


class TestResultOut(BaseModel):
    ok: bool
    latency_ms: int
    detail: str | None = None
    error: str | None = None


def _out(i: Integration) -> IntegrationOut:
    return IntegrationOut(
        id=i.id,
        kind=i.kind,
        external_id=i.external_id,
        base_url=i.base_url,
        status=i.status,
        last_checked_at=i.last_checked_at,
        last_error=i.last_error,
        has_secret=i.secret_ref is not None,
    )


def _reject_untestable(kind: IntegrationKind) -> None:
    if kind not in _TESTABLE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"{kind.value} is not a per-project testable integration",
        )


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    project_id: UUID,
    repo_id: UUID,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[IntegrationOut]:
    return [_out(i) for i in await integration_service.list_integrations(session, repo_id)]


@router.put("/{kind}", response_model=IntegrationOut)
async def upsert_integration(
    project_id: UUID,
    repo_id: UUID,
    kind: IntegrationKind,
    body: IntegrationUpsert,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
    secret_store: SecretStore = Depends(get_secret_store),
) -> IntegrationOut:
    _reject_untestable(kind)
    integ = await integration_service.upsert_integration(
        session,
        actor_id=admin.id,
        project_repo_id=repo_id,
        kind=kind,
        external_id=body.external_id,
        base_url=body.base_url,
        config=body.config,
        token=body.token,
        secret_store=secret_store,
    )
    await session.commit()
    return _out(integ)


@router.post("/{kind}/test", response_model=TestResultOut)
async def test_integration(
    project_id: UUID,
    repo_id: UUID,
    kind: IntegrationKind,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
    secret_store: SecretStore = Depends(get_secret_store),
    http_client: Any = Depends(get_http_client),
) -> TestResultOut:
    _reject_untestable(kind)
    try:
        result = await integration_service.test_integration(
            session,
            actor_id=admin.id,
            project_repo_id=repo_id,
            kind=kind,
            secret_store=secret_store,
            http_client=http_client,
        )
        await session.commit()
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not result.ok:
        # Surface the provider's real message inline (PLAN.md §5.1). 422 literal
        # avoids the starlette constant-rename deprecation warning.
        raise HTTPException(422, detail=result.model_dump())
    return TestResultOut(**result.model_dump())

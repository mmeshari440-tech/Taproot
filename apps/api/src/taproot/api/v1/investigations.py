"""Investigation endpoints (T-17).

Submit → 202 + enqueue; list (paginated, scoped to the caller); get + cancel with
per-investigation authorization (owner or admin). SSE streaming is T-18.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.api.v1.deps import get_current_user, get_job_queue, get_principal
from taproot.core.exceptions import NotFoundError, PreconditionError
from taproot.core.security import Principal
from taproot.db.models import Investigation, InvestigationStatus, User
from taproot.db.session import get_session
from taproot.services import investigation_service
from taproot.workers.queue import JobQueue

router = APIRouter(prefix="/investigations", tags=["investigations"])


class InvestigationCreate(BaseModel):
    project_id: UUID
    error_text: str
    time_window_days: int = 7


class InvestigationOut(BaseModel):
    id: UUID
    project_id: UUID
    error_text: str
    status: InvestigationStatus
    time_window_days: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None


class InvestigationPage(BaseModel):
    items: list[InvestigationOut]
    total: int
    page: int
    page_size: int


def _out(i: Investigation) -> InvestigationOut:
    return InvestigationOut(
        id=i.id,
        project_id=i.project_id,
        error_text=i.error_text,
        status=i.status,
        time_window_days=i.time_window_days,
        created_at=i.created_at,
        started_at=i.started_at,
        finished_at=i.finished_at,
        duration_ms=i.duration_ms,
    )


def _authorize(inv: Investigation, user: User, principal: Principal) -> None:
    """Owner or admin may view/cancel a specific investigation (ARCHITECTURE.md §8.1)."""
    if inv.created_by != user.id and not principal.has_role("platform-admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your investigation")


@router.post("", response_model=InvestigationOut, status_code=status.HTTP_202_ACCEPTED)
async def submit(
    body: InvestigationCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    queue: JobQueue = Depends(get_job_queue),
) -> InvestigationOut:
    try:
        inv = await investigation_service.create_investigation(
            session,
            actor_id=user.id,
            project_id=body.project_id,
            error_text=body.error_text,
            time_window_days=body.time_window_days,
        )
        await session.commit()
    except PreconditionError as exc:
        raise HTTPException(422, detail=str(exc)) from exc

    await queue.enqueue_investigation(inv.id)
    return _out(inv)


@router.get("", response_model=InvestigationPage)
async def list_investigations(
    project_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InvestigationPage:
    items, total = await investigation_service.list_investigations(
        session, created_by=user.id, project_id=project_id, page=page, page_size=page_size
    )
    return InvestigationPage(
        items=[_out(i) for i in items], total=total, page=page, page_size=page_size
    )


@router.get("/{investigation_id}", response_model=InvestigationOut)
async def get_investigation(
    investigation_id: UUID,
    user: User = Depends(get_current_user),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> InvestigationOut:
    try:
        inv = await investigation_service.get_investigation(session, investigation_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _authorize(inv, user, principal)
    return _out(inv)


@router.post("/{investigation_id}/cancel", response_model=InvestigationOut)
async def cancel_investigation(
    investigation_id: UUID,
    user: User = Depends(get_current_user),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> InvestigationOut:
    try:
        inv = await investigation_service.get_investigation(session, investigation_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _authorize(inv, user, principal)
    inv = await investigation_service.cancel_investigation(session, investigation_id)
    await session.commit()
    return _out(inv)

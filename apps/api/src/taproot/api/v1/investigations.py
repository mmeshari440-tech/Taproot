"""Investigation endpoints (T-17).

Submit → 202 + enqueue; list (paginated, scoped to the caller); get + cancel with
per-investigation authorization (owner or admin). SSE streaming is T-18.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.api.v1.deps import (
    get_current_user,
    get_event_bus,
    get_job_queue,
    get_principal,
    get_token_validator,
)
from taproot.core.events import EventBus
from taproot.core.exceptions import AuthenticationError, NotFoundError, PreconditionError
from taproot.core.security import Principal, TokenValidator
from taproot.db.models import (
    Investigation,
    InvestigationStatus,
    InvestigationStep,
    StepStatus,
    User,
)
from taproot.db.session import get_session
from taproot.services import investigation_service
from taproot.services.user_service import upsert_user
from taproot.workers.queue import JobQueue

router = APIRouter(prefix="/investigations", tags=["investigations"])

_TERMINAL = {
    InvestigationStatus.DONE,
    InvestigationStatus.FAILED,
    InvestigationStatus.CANCELLED,
}


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


# --- SSE streaming (T-18, req 11) ------------------------------------------
def _sse(seq: int | None, data: dict[str, Any]) -> str:
    payload = json.dumps(data)
    if seq is not None:
        return f"id: {seq}\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


def _step_event(step: InvestigationStep) -> dict[str, Any]:
    if step.status == StepStatus.running:
        return {"type": "step.start", "seq": step.seq, "node": step.node, "title": step.title}
    return {
        "type": "step.finish",
        "seq": step.seq,
        "node": step.node,
        "status": step.status.value,
        "summary": step.summary,
        "metrics": {},
    }


async def _event_stream(
    session: AsyncSession,
    event_bus: EventBus,
    inv: Investigation,
    last_id: int,
) -> AsyncIterator[str]:
    async with event_bus.subscribe(inv.id) as live:
        # Replay persisted steps after Last-Event-ID (DB is the source of truth).
        steps = (
            await session.execute(
                select(InvestigationStep)
                .where(
                    InvestigationStep.investigation_id == inv.id,
                    InvestigationStep.seq > last_id,
                )
                .order_by(InvestigationStep.seq)
            )
        ).scalars().all()
        max_seq = last_id
        for step in steps:
            yield _sse(step.seq, _step_event(step))
            max_seq = max(max_seq, step.seq)

        # If the run already finished, replay is enough — close the stream.
        await session.refresh(inv)
        if inv.status in _TERMINAL:
            yield _sse(None, {"type": "done"})
            return

        # Otherwise relay live events, with a heartbeat to keep proxies open.
        while True:
            try:
                event = await asyncio.wait_for(live.__anext__(), timeout=15.0)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            seq = event.get("seq")
            if isinstance(seq, int):
                if seq <= max_seq:
                    continue
                max_seq = seq
            yield _sse(seq if isinstance(seq, int) else None, event)
            if event.get("type") == "done":
                return


@router.get("/{investigation_id}/stream")
async def stream(
    investigation_id: UUID,
    request: Request,
    access_token: str = Query(..., description="Bearer token (EventSource cannot set headers)"),
    validator: TokenValidator = Depends(get_token_validator),
    session: AsyncSession = Depends(get_session),
    event_bus: EventBus = Depends(get_event_bus),
) -> StreamingResponse:
    # EventSource can't send an Authorization header, so the token is a query param.
    try:
        principal = await validator.validate(access_token)
    except AuthenticationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = await upsert_user(session, principal)
    await session.commit()

    try:
        inv = await investigation_service.get_investigation(session, investigation_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _authorize(inv, user, principal)

    last_id = int(request.headers.get("Last-Event-ID") or 0)
    return StreamingResponse(
        _event_stream(session, event_bus, inv, last_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

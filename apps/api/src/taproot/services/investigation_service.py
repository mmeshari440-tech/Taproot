"""Investigation lifecycle service (T-17).

Creation is gated on a verified Elasticsearch integration (PLAN.md §5.1). Listing
is scoped to the caller's own investigations; per-project ACLs arrive with the
access model in a later sprint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.exceptions import NotFoundError, PreconditionError
from taproot.db.models import (
    Integration,
    IntegrationKind,
    IntegrationStatus,
    Investigation,
    InvestigationStatus,
)

_ACTIVE = (InvestigationStatus.QUEUED, InvestigationStatus.RUNNING)


async def _elastic_ok(session: AsyncSession, project_id: UUID) -> bool:
    status = (
        await session.execute(
            select(Integration.status).where(
                Integration.project_id == project_id,
                Integration.kind == IntegrationKind.ELASTIC,
            )
        )
    ).scalar_one_or_none()
    return status == IntegrationStatus.OK


async def create_investigation(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    project_id: UUID,
    error_text: str,
    time_window_days: int = 7,
) -> Investigation:
    if not await _elastic_ok(session, project_id):
        raise PreconditionError(
            "This project's Elasticsearch integration is not verified (status must be OK) "
            "before investigations can run."
        )
    inv = Investigation(
        project_id=project_id,
        created_by=actor_id,
        error_text=error_text,
        time_window_days=time_window_days,
        status=InvestigationStatus.QUEUED,
    )
    session.add(inv)
    await session.flush()
    return inv


async def get_investigation(session: AsyncSession, investigation_id: UUID) -> Investigation:
    inv = await session.get(Investigation, investigation_id)
    if inv is None:
        raise NotFoundError(f"Investigation {investigation_id} not found")
    return inv


async def list_investigations(
    session: AsyncSession,
    *,
    created_by: UUID | None,
    project_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Investigation], int]:
    filters = [Investigation.created_by == created_by]
    if project_id is not None:
        filters.append(Investigation.project_id == project_id)

    total = (
        await session.execute(select(func.count()).select_from(Investigation).where(*filters))
    ).scalar_one()

    rows = (
        await session.execute(
            select(Investigation)
            .where(*filters)
            .order_by(Investigation.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).scalars().all()
    return list(rows), int(total)


async def cancel_investigation(
    session: AsyncSession, investigation_id: UUID
) -> Investigation:
    inv = await get_investigation(session, investigation_id)
    if inv.status in _ACTIVE:
        inv.status = InvestigationStatus.CANCELLED
        inv.finished_at = datetime.now(UTC)
        await session.flush()
    return inv

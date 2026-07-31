from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.exceptions import PreconditionError
from taproot.db.models import (
    Integration,
    IntegrationKind,
    IntegrationStatus,
    InvestigationStatus,
    Project,
    User,
)
from taproot.services import investigation_service as svc


async def _project(session: AsyncSession, *, elastic: IntegrationStatus | None) -> Project:
    project = Project(name="P", slug=f"p-{id(session)}")
    session.add(project)
    await session.flush()
    if elastic is not None:
        session.add(
            Integration(project_id=project.id, kind=IntegrationKind.ELASTIC, status=elastic)
        )
        await session.flush()
    return project


async def test_create_requires_elastic_ok(session: AsyncSession) -> None:
    project = await _project(session, elastic=IntegrationStatus.FAILED)
    with pytest.raises(PreconditionError):
        await svc.create_investigation(
            session, actor_id=None, project_id=project.id, error_text="NPE"
        )


async def test_create_succeeds_when_elastic_ok(session: AsyncSession) -> None:
    project = await _project(session, elastic=IntegrationStatus.OK)
    inv = await svc.create_investigation(
        session, actor_id=None, project_id=project.id, error_text="NPE"
    )
    assert inv.status == InvestigationStatus.QUEUED


async def test_list_is_scoped_and_paginated(session: AsyncSession) -> None:
    project = await _project(session, elastic=IntegrationStatus.OK)
    alice = User(keycloak_sub="alice", email="a@x.co")
    bob = User(keycloak_sub="bob", email="b@x.co")
    session.add_all([alice, bob])
    await session.flush()

    for i in range(3):
        await svc.create_investigation(
            session, actor_id=alice.id, project_id=project.id, error_text=f"a{i}"
        )
    await svc.create_investigation(
        session, actor_id=bob.id, project_id=project.id, error_text="b0"
    )
    await session.commit()

    items, total = await svc.list_investigations(session, created_by=alice.id, page=1, page_size=2)
    assert total == 3  # only alice's
    assert len(items) == 2  # page size


async def test_cancel_only_affects_active(session: AsyncSession) -> None:
    project = await _project(session, elastic=IntegrationStatus.OK)
    inv = await svc.create_investigation(
        session, actor_id=None, project_id=project.id, error_text="x"
    )
    await session.commit()

    cancelled = await svc.cancel_investigation(session, inv.id)
    assert cancelled.status == InvestigationStatus.CANCELLED

    # Cancelling a terminal investigation is a no-op.
    inv.status = InvestigationStatus.DONE
    await session.commit()
    again = await svc.cancel_investigation(session, inv.id)
    assert again.status == InvestigationStatus.DONE

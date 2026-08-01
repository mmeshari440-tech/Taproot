from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.db.models import (
    Integration,
    IntegrationKind,
    IntegrationStatus,
    Investigation,
    InvestigationStatus,
    Project,
    ProjectRepo,
    RepoKind,
    User,
)


async def test_create_full_project_graph(session: AsyncSession) -> None:
    user = User(keycloak_sub="sub-1", email="a@b.co", display_name="Alice")
    session.add(user)
    await session.flush()

    project = Project(name="Payments", slug="payments", created_by=user.id)
    session.add(project)
    await session.flush()

    repo = ProjectRepo(
        project_id=project.id,
        gitlab_project_id=42,
        name="be-payments",
        kind=RepoKind.BE,
        org_package_prefixes=["com.acme."],
    )
    session.add(repo)
    await session.flush()
    session.add(
        Integration(
            project_repo_id=repo.id,
            kind=IntegrationKind.ELASTIC,
            external_id="logs-*",
            secret_ref="local://abc",
        )
    )
    inv = Investigation(project_id=project.id, created_by=user.id, error_text="NullPointer")
    session.add(inv)
    await session.commit()

    # Defaults applied.
    assert inv.status == InvestigationStatus.QUEUED
    assert inv.time_window_days == 7

    loaded = (
        await session.execute(select(Integration).where(Integration.project_repo_id == repo.id))
    ).scalar_one()
    assert loaded.status == IntegrationStatus.UNVERIFIED
    assert loaded.secret_ref == "local://abc"

    repo = (
        await session.execute(select(ProjectRepo).where(ProjectRepo.project_id == project.id))
    ).scalar_one()
    assert repo.org_package_prefixes == ["com.acme."]


async def test_integration_unique_per_kind(session: AsyncSession) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    project = Project(name="P", slug="p")
    session.add(project)
    await session.flush()
    repo = ProjectRepo(project_id=project.id, gitlab_project_id=1, name="app")
    session.add(repo)
    await session.flush()
    session.add(Integration(project_repo_id=repo.id, kind=IntegrationKind.SENTRY))
    await session.flush()
    session.add(Integration(project_repo_id=repo.id, kind=IntegrationKind.SENTRY))
    with pytest.raises(IntegrityError):
        await session.flush()

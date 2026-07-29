from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.models import GitLabProject
from taproot.db.models import AuditLog, ProjectRepo, RepoKind
from taproot.services import project_service as svc


@pytest.mark.parametrize(
    ("name", "topics", "expected"),
    [
        ("be-payments", [], RepoKind.BE),
        ("web-portal", [], RepoKind.FE),
        ("checkout", ["frontend"], RepoKind.FE),
        ("orders-api", [], RepoKind.BE),
        ("payments", ["backend"], RepoKind.BE),
        ("infra-scripts", [], RepoKind.OTHER),
    ],
)
def test_classify_repo(name: str, topics: list[str], expected: RepoKind) -> None:
    assert svc.classify_repo(name, topics) == expected


def test_slugify() -> None:
    assert svc.slugify("My Cool Project!") == "my-cool-project"
    assert svc.slugify("!!!") == "project"


async def test_create_project_writes_audit(session: AsyncSession) -> None:
    project = await svc.create_project(
        session, actor_id=None, name="Payments", gitlab_group_path="acme/payments"
    )
    await session.commit()
    assert project.slug == "payments"

    audits = (await session.execute(select(AuditLog))).scalars().all()
    assert any(a.action == "project.create" for a in audits)


class _FakeGitLab:
    def __init__(self, projects: list[GitLabProject]) -> None:
        self._projects = projects

    async def list_group_projects(self, group_id: int) -> list[GitLabProject]:
        return self._projects


async def test_sync_repos_classifies_and_is_idempotent(session: AsyncSession) -> None:
    project = await svc.create_project(
        session, actor_id=None, name="Payments", gitlab_group_id=99
    )
    fake = _FakeGitLab(
        [
            GitLabProject(id=1, name="be-payments", path_with_namespace="a/be", topics=[]),
            GitLabProject(id=2, name="web-portal", path_with_namespace="a/web", topics=[]),
        ]
    )
    repos = await svc.sync_repos(session, actor_id=None, project=project, gitlab=fake)  # type: ignore[arg-type]
    await session.commit()
    kinds = {r.name: r.kind for r in repos}
    assert kinds == {"be-payments": RepoKind.BE, "web-portal": RepoKind.FE}

    # Re-sync must not duplicate rows.
    await svc.sync_repos(session, actor_id=None, project=project, gitlab=fake)  # type: ignore[arg-type]
    await session.commit()
    count = (await session.execute(select(func.count()).select_from(ProjectRepo))).scalar_one()
    assert count == 2


async def test_set_repo_kind_override(session: AsyncSession) -> None:
    project = await svc.create_project(session, actor_id=None, name="P", gitlab_group_id=1)
    fake = _FakeGitLab(
        [GitLabProject(id=1, name="ambiguous", path_with_namespace="a/x", topics=[])]
    )
    [repo] = await svc.sync_repos(session, actor_id=None, project=project, gitlab=fake)  # type: ignore[arg-type]
    await session.commit()
    assert repo.kind == RepoKind.OTHER

    updated = await svc.set_repo_kind(
        session,
        actor_id=None,
        repo_id=repo.id,
        kind=RepoKind.BE,
        org_package_prefixes=["com.acme."],
    )
    await session.commit()
    assert updated.kind == RepoKind.BE
    assert updated.org_package_prefixes == ["com.acme."]

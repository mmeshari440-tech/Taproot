"""Project + repo management (T-09 sync, T-10 CRUD), all audit-logged.

Depends on ``db``, ``integrations``, ``core`` (ARCHITECTURE.md §3).
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.exceptions import NotFoundError
from taproot.core.models import RepoRef
from taproot.db.models import (
    AuditLog,
    Integration,
    IntegrationKind,
    IntegrationStatus,
    Project,
    ProjectRepo,
    RepoKind,
)
from taproot.integrations.gitlab import GitLabClient

# --- FE/BE classification heuristic (req 1) --------------------------------
# Token-based: split the repo name on separators and match whole tokens, so
# prefix/suffix styles ("be-payments", "web-portal") both classify correctly.
_FE_TOKENS = {
    "frontend", "fe", "react", "vue", "angular", "web", "ui", "spa", "portal", "dashboard",
}
_BE_TOKENS = {"backend", "be", "api", "service", "svc", "server", "worker", "gateway"}


def classify_repo(name: str, topics: list[str]) -> RepoKind:
    """Best-effort FE/BE/OTHER classification; admins can override (T-10)."""
    tokens = set(re.split(r"[-_/\s]+", name.lower())) | {t.lower() for t in topics}
    if tokens & _FE_TOKENS:
        return RepoKind.FE
    if tokens & _BE_TOKENS:
        return RepoKind.BE
    return RepoKind.OTHER


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


async def _audit(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: str,
    meta: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=meta,
        )
    )


async def create_project(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    name: str,
    gitlab_group_path: str | None = None,
    gitlab_group_id: int | None = None,
) -> Project:
    project = Project(
        name=name,
        slug=slugify(name),
        gitlab_group_path=gitlab_group_path,
        gitlab_group_id=gitlab_group_id,
        created_by=actor_id,
    )
    session.add(project)
    await session.flush()
    await _audit(
        session,
        actor_id=actor_id,
        action="project.create",
        entity_type="project",
        entity_id=str(project.id),
        meta={"name": name, "gitlab_group_path": gitlab_group_path},
    )
    return project


async def list_projects(session: AsyncSession, *, active_only: bool = False) -> list[Project]:
    stmt = select(Project).order_by(Project.created_at.desc())
    if active_only:
        stmt = stmt.where(Project.is_active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def has_healthy_elastic(session: AsyncSession, project_id: UUID) -> bool:
    """True if any of the project's apps (repos) has a verified Elastic
    integration (ADR-0002 — integrations are per-repo)."""
    count = (
        await session.execute(
            select(func.count())
            .select_from(Integration)
            .join(ProjectRepo, Integration.project_repo_id == ProjectRepo.id)
            .where(
                ProjectRepo.project_id == project_id,
                Integration.kind == IntegrationKind.ELASTIC,
                Integration.status == IntegrationStatus.OK,
            )
        )
    ).scalar_one()
    return count > 0


async def repo_refs_for_project(session: AsyncSession, project_id: UUID) -> list[RepoRef]:
    """Flatten the project's repos into `RepoRef`s for the agent's `code_locate`
    node (T-25), so the agent resolves frames→repo/ref without touching `db`."""
    repos = (
        (await session.execute(select(ProjectRepo).where(ProjectRepo.project_id == project_id)))
        .scalars()
        .all()
    )
    return [
        RepoRef(
            name=repo.name,
            gitlab_project_id=repo.gitlab_project_id,
            default_branch=repo.default_branch,
            kind=repo.kind.value,
            org_package_prefixes=repo.org_package_prefixes or [],
            web_url=repo.web_url,
        )
        for repo in repos
    ]


async def get_project(session: AsyncSession, project_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


async def deactivate_project(
    session: AsyncSession, *, actor_id: UUID | None, project_id: UUID
) -> Project:
    project = await get_project(session, project_id)
    project.is_active = False
    await _audit(
        session,
        actor_id=actor_id,
        action="project.deactivate",
        entity_type="project",
        entity_id=str(project.id),
    )
    return project


async def sync_repos(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    project: Project,
    gitlab: GitLabClient,
) -> list[ProjectRepo]:
    """Pull the group's projects from GitLab into ``project_repos`` (req 1).

    Existing repos are updated in place (preserving admin ``kind`` overrides is a
    Phase-2 nicety; for now a re-sync refreshes classification).
    """
    if project.gitlab_group_id is None:
        raise NotFoundError("Project has no linked GitLab group id")

    remote = await gitlab.list_group_projects(project.gitlab_group_id)
    existing = {
        r.gitlab_project_id: r
        for r in (
            await session.execute(
                select(ProjectRepo).where(ProjectRepo.project_id == project.id)
            )
        ).scalars()
    }

    result: list[ProjectRepo] = []
    for gp in remote:
        repo = existing.get(gp.id)
        kind = classify_repo(gp.name, gp.topics)
        if repo is None:
            repo = ProjectRepo(
                project_id=project.id,
                gitlab_project_id=gp.id,
                name=gp.name,
                kind=kind,
                default_branch=gp.default_branch,
                web_url=gp.web_url,
            )
            session.add(repo)
        else:
            repo.name = gp.name
            repo.default_branch = gp.default_branch
            repo.web_url = gp.web_url
        result.append(repo)

    await session.flush()
    await _audit(
        session,
        actor_id=actor_id,
        action="project.sync_repos",
        entity_type="project",
        entity_id=str(project.id),
        meta={"repo_count": len(result)},
    )
    return result


async def set_repo_kind(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    repo_id: UUID,
    kind: RepoKind,
    org_package_prefixes: list[str] | None = None,
) -> ProjectRepo:
    repo = await session.get(ProjectRepo, repo_id)
    if repo is None:
        raise NotFoundError(f"Repo {repo_id} not found")
    repo.kind = kind
    if org_package_prefixes is not None:
        repo.org_package_prefixes = org_package_prefixes
    await _audit(
        session,
        actor_id=actor_id,
        action="repo.set_kind",
        entity_type="project_repo",
        entity_id=str(repo.id),
        meta={"kind": kind.value},
    )
    return repo

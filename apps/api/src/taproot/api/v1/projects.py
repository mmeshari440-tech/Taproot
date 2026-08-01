"""Project + repo admin endpoints (T-09 group discovery, T-10 CRUD).

Writes require ``platform-admin`` and are audit-logged; reads are available to any
authenticated user (their project access is scoped further in Sprint 2).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.api.v1.deps import (
    get_current_admin,
    get_current_user,
    get_gitlab_client,
)
from taproot.core.exceptions import IntegrationError, NotFoundError
from taproot.db.models import Project, ProjectRepo, RepoKind, User
from taproot.db.session import get_session
from taproot.integrations.gitlab import GitLabClient
from taproot.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


# --- schemas ----------------------------------------------------------------
class GitLabGroupOut(BaseModel):
    id: int
    name: str
    full_path: str
    web_url: str | None = None


class ProjectCreate(BaseModel):
    name: str
    gitlab_group_path: str | None = None
    gitlab_group_id: int | None = None


class ProjectOut(BaseModel):
    id: UUID
    name: str
    slug: str
    gitlab_group_path: str | None
    is_active: bool
    elastic_ok: bool = False  # any app has a verified Elastic integration (ADR-0002)


class RepoOut(BaseModel):
    id: UUID
    gitlab_project_id: int
    name: str
    kind: RepoKind
    default_branch: str
    web_url: str | None
    org_package_prefixes: list[str]


class RepoKindUpdate(BaseModel):
    kind: RepoKind
    org_package_prefixes: list[str] | None = None


def _project_out(p: Project, *, elastic_ok: bool = False) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        slug=p.slug,
        gitlab_group_path=p.gitlab_group_path,
        is_active=p.is_active,
        elastic_ok=elastic_ok,
    )


def _repo_out(r: ProjectRepo) -> RepoOut:
    return RepoOut(
        id=r.id,
        gitlab_project_id=r.gitlab_project_id,
        name=r.name,
        kind=r.kind,
        default_branch=r.default_branch,
        web_url=r.web_url,
        org_package_prefixes=r.org_package_prefixes,
    )


# --- GitLab discovery (req 1) ----------------------------------------------
@router.get("/gitlab-groups", response_model=list[GitLabGroupOut])
async def gitlab_groups(
    search: str | None = Query(default=None),
    _: User = Depends(get_current_admin),
    gitlab: GitLabClient = Depends(get_gitlab_client),
) -> list[GitLabGroupOut]:
    try:
        groups = await gitlab.list_groups(search)
    except IntegrationError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return [GitLabGroupOut(**g.model_dump()) for g in groups]


# --- CRUD -------------------------------------------------------------------
@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    try:
        project = await project_service.create_project(
            session,
            actor_id=admin.id,
            name=body.name,
            gitlab_group_path=body.gitlab_group_path,
            gitlab_group_id=body.gitlab_group_id,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Project slug already exists") from exc
    return _project_out(project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectOut]:
    projects = await project_service.list_projects(session)
    return [
        _project_out(p, elastic_ok=await project_service.has_healthy_elastic(session, p.id))
        for p in projects
    ]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    try:
        project = await project_service.get_project(session, project_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    ok = await project_service.has_healthy_elastic(session, project.id)
    return _project_out(project, elastic_ok=ok)


@router.get("/{project_id}/repos", response_model=list[RepoOut])
async def list_repos(
    project_id: UUID,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[RepoOut]:
    project = await project_service.get_project(session, project_id)
    await session.refresh(project, attribute_names=["repos"])
    return [_repo_out(r) for r in project.repos]


@router.post("/{project_id}/sync-repos", response_model=list[RepoOut])
async def sync_repos(
    project_id: UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
    gitlab: GitLabClient = Depends(get_gitlab_client),
) -> list[RepoOut]:
    try:
        project = await project_service.get_project(session, project_id)
        repos = await project_service.sync_repos(
            session, actor_id=admin.id, project=project, gitlab=gitlab
        )
        await session.commit()
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IntegrationError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return [_repo_out(r) for r in repos]


@router.patch("/{project_id}/repos/{repo_id}", response_model=RepoOut)
async def update_repo_kind(
    project_id: UUID,
    repo_id: UUID,
    body: RepoKindUpdate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RepoOut:
    try:
        repo = await project_service.set_repo_kind(
            session,
            actor_id=admin.id,
            repo_id=repo_id,
            kind=body.kind,
            org_package_prefixes=body.org_package_prefixes,
        )
        await session.commit()
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _repo_out(repo)


@router.post("/{project_id}/deactivate", response_model=ProjectOut)
async def deactivate_project(
    project_id: UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    try:
        project = await project_service.deactivate_project(
            session, actor_id=admin.id, project_id=project_id
        )
        await session.commit()
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _project_out(project)

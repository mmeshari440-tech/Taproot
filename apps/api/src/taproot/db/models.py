"""SQLAlchemy models — the full data model (PLAN.md §4 / ARCHITECTURE.md §7).

Roles are **not** stored: they come from the JWT. The ``users`` row exists only
for audit joins.

Portable types are used deliberately (``Uuid``, generic ``JSON``,
timezone-aware ``DateTime``) so the same models run on Postgres in production and
SQLite in-memory for unit tests. Provider-specific tuning (JSONB, partial
indexes) belongs in migrations, not here.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON as SA_JSON,
)
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from taproot.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Enumerations -----------------------------------------------------------
class RepoKind(enum.StrEnum):
    FE = "FE"
    BE = "BE"
    OTHER = "OTHER"


class IntegrationKind(enum.StrEnum):
    ELASTIC = "ELASTIC"
    SENTRY = "SENTRY"
    APPDYNAMICS = "APPDYNAMICS"
    KEYCLOAK = "KEYCLOAK"


class IntegrationStatus(enum.StrEnum):
    UNVERIFIED = "UNVERIFIED"
    OK = "OK"
    FAILED = "FAILED"


class InvestigationStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(enum.StrEnum):
    running = "running"
    ok = "ok"
    failed = "failed"
    skipped = "skipped"


class Severity(enum.StrEnum):
    BLOCKER = "BLOCKER"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


def _enum(py_enum: type[enum.Enum]) -> Enum:
    # native_enum=False → portable VARCHAR + CHECK, works on SQLite and Postgres.
    return Enum(py_enum, native_enum=False, validate_strings=True)


# --- Tables -----------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    keycloak_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    gitlab_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gitlab_group_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    repos: Mapped[list[ProjectRepo]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    integrations: Mapped[list[Integration]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectRepo(Base):
    __tablename__ = "project_repos"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    gitlab_project_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[RepoKind] = mapped_column(_enum(RepoKind), default=RepoKind.OTHER)
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    web_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Per-project org package prefixes for code_locate (ARCHITECTURE.md §6.5).
    org_package_prefixes: Mapped[list[str]] = mapped_column(SA_JSON, default=list)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="repos")


class Integration(Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("project_id", "kind", name="integrations_project_kind"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[IntegrationKind] = mapped_column(_enum(IntegrationKind))
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(SA_JSON, default=dict)
    # Vault path — NEVER the token itself.
    secret_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[IntegrationStatus] = mapped_column(
        _enum(IntegrationStatus), default=IntegrationStatus.UNVERIFIED
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="integrations")


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (
        # Supports the history endpoint: list by project, newest first.
        Index("ix_investigations_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    error_text: Mapped[str] = mapped_column(Text)
    time_window_days: Mapped[int] = mapped_column(Integer, default=7)
    status: Mapped[InvestigationStatus] = mapped_column(
        _enum(InvestigationStatus), default=InvestigationStatus.QUEUED, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    steps: Mapped[list[InvestigationStep]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    result: Mapped[InvestigationResult | None] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", uselist=False
    )


class InvestigationStep(Base):
    __tablename__ = "investigation_steps"
    __table_args__ = (
        UniqueConstraint("investigation_id", "seq", name="investigation_steps_inv_seq"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    investigation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)  # SSE Last-Event-ID
    node: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus), default=StepStatus.running)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON, nullable=True)  # REDACTED

    investigation: Mapped[Investigation] = relationship(back_populates="steps")


class InvestigationResult(Base):
    __tablename__ = "investigation_result"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    investigation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("investigations.id", ondelete="CASCADE"), unique=True, index=True
    )
    severity: Mapped[Severity] = mapped_column(_enum(Severity))
    severity_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_evidence: Mapped[list[Any] | None] = mapped_column(SA_JSON, nullable=True)
    code_locations: Mapped[list[Any] | None] = mapped_column(SA_JSON, nullable=True)
    suggested_fixes: Mapped[list[Any] | None] = mapped_column(SA_JSON, nullable=True)
    third_party_involved: Mapped[bool] = mapped_column(Boolean, default=False)
    third_party_details: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON, nullable=True)
    occurrence_series: Mapped[list[Any] | None] = mapped_column(SA_JSON, nullable=True)  # chart
    raw_model_output: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON, nullable=True)

    investigation: Mapped[Investigation] = relationship(back_populates="result")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


__all__ = [
    "AuditLog",
    "Base",
    "Integration",
    "IntegrationKind",
    "IntegrationStatus",
    "Investigation",
    "InvestigationResult",
    "InvestigationStatus",
    "InvestigationStep",
    "Project",
    "ProjectRepo",
    "RepoKind",
    "Severity",
    "StepStatus",
    "User",
]

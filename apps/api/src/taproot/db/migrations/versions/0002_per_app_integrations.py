"""per-application integrations (ADR-0002)

Move integrations from per-project to per-repo (each app has its own Elastic
index + Sentry account). Pre-release, no deployed data.

Revision ID: a1b2c3d4e5f6
Revises: 480f96f6f857
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "480f96f6f857"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("integrations_project_kind", "integrations", type_="unique")
    op.drop_index("ix_integrations_project_id", table_name="integrations")
    op.drop_constraint("fk_integrations_project_id_projects", "integrations", type_="foreignkey")
    op.drop_column("integrations", "project_id")

    op.add_column("integrations", sa.Column("project_repo_id", sa.Uuid(), nullable=False))
    op.create_index("ix_integrations_project_repo_id", "integrations", ["project_repo_id"])
    op.create_foreign_key(
        "fk_integrations_project_repo_id_project_repos",
        "integrations",
        "project_repos",
        ["project_repo_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "integrations_project_repo_kind", "integrations", ["project_repo_id", "kind"]
    )


def downgrade() -> None:
    op.drop_constraint("integrations_project_repo_kind", "integrations", type_="unique")
    op.drop_constraint(
        "fk_integrations_project_repo_id_project_repos", "integrations", type_="foreignkey"
    )
    op.drop_index("ix_integrations_project_repo_id", table_name="integrations")
    op.drop_column("integrations", "project_repo_id")

    op.add_column("integrations", sa.Column("project_id", sa.Uuid(), nullable=False))
    op.create_index("ix_integrations_project_id", "integrations", ["project_id"])
    op.create_foreign_key(
        "fk_integrations_project_id_projects",
        "integrations",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("integrations_project_kind", "integrations", ["project_id", "kind"])

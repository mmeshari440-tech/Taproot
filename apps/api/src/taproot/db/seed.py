"""Seed script (T-03): create one demo user + one demo project.

Idempotent — safe to run repeatedly. Invoked by ``make seed``.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from taproot.core.logging import configure_logging, get_logger
from taproot.db.models import Project, User
from taproot.db.session import get_sessionmaker

_log = get_logger(__name__)

DEMO_USER_SUB = "demo-user"
DEMO_PROJECT_SLUG = "demo"


async def seed() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        user = (
            await session.execute(select(User).where(User.keycloak_sub == DEMO_USER_SUB))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                keycloak_sub=DEMO_USER_SUB,
                email="demo@taproot.local",
                display_name="Demo Admin",
            )
            session.add(user)
            await session.flush()
            _log.info("seed_user_created", user_id=str(user.id))

        project = (
            await session.execute(select(Project).where(Project.slug == DEMO_PROJECT_SLUG))
        ).scalar_one_or_none()
        if project is None:
            project = Project(
                name="Demo Project",
                slug=DEMO_PROJECT_SLUG,
                description="Seeded demo project.",
                created_by=user.id,
            )
            session.add(project)
            _log.info("seed_project_created", slug=DEMO_PROJECT_SLUG)

        await session.commit()
    _log.info("seed_complete")


def main() -> None:
    configure_logging()
    asyncio.run(seed())


if __name__ == "__main__":
    main()

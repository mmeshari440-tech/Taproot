"""User persistence for audit joins (ARCHITECTURE.md §8.1).

Roles are never stored — only an identity record, upserted on first authenticated
request and refreshed on each login.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.security import Principal
from taproot.db.models import User


async def upsert_user(session: AsyncSession, principal: Principal) -> User:
    """Insert the user on first sight, otherwise refresh identity + last login."""
    user = (
        await session.execute(select(User).where(User.keycloak_sub == principal.sub))
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if user is None:
        user = User(
            keycloak_sub=principal.sub,
            email=principal.email or "",
            display_name=principal.name,
            last_login_at=now,
        )
        session.add(user)
    else:
        user.last_login_at = now
        if principal.email:
            user.email = principal.email
        if principal.name:
            user.display_name = principal.name

    await session.flush()
    return user

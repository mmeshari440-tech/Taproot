"""Auth-scoped endpoints (T-07).

`/me` exercises authenticated identity + user upsert; `/admin/ping` demonstrates
role-based authorization (403 for non-admins).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from taproot.api.v1.deps import get_current_user, get_principal, require_role
from taproot.core.security import Principal
from taproot.db.models import User

router = APIRouter(tags=["auth"])

require_admin = require_role("platform-admin")


class UserOut(BaseModel):
    id: UUID
    sub: str
    email: str
    display_name: str | None
    roles: list[str]


@router.get("/me", response_model=UserOut)
async def me(
    user: User = Depends(get_current_user),
    principal: Principal = Depends(get_principal),
) -> UserOut:
    return UserOut(
        id=user.id,
        sub=principal.sub,
        email=user.email,
        display_name=user.display_name,
        roles=principal.roles,
    )


@router.get("/admin/ping")
async def admin_ping(
    principal: Principal = Depends(require_admin),
) -> dict[str, object]:
    return {"ok": True, "sub": principal.sub}

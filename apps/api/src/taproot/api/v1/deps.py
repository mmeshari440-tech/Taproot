"""FastAPI auth dependencies (ARCHITECTURE.md §8.1).

401 (bad/expired/missing token) and 403 (valid token, wrong role) are kept
strictly distinct. The token validator is injected via ``get_token_validator`` so
tests can override it with a static-key validator (no Redis/Keycloak needed).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.cache import RedisCache
from taproot.core.config import Settings, get_settings
from taproot.core.exceptions import AuthenticationError
from taproot.core.security import (
    Principal,
    TokenValidator,
    build_jwks_provider,
    issuer_for,
)
from taproot.db.models import User
from taproot.db.session import get_session
from taproot.services.user_service import upsert_user

_bearer = HTTPBearer(auto_error=False)


@lru_cache
def _default_validator() -> TokenValidator:
    """Build the production validator (httpx + Redis-cached JWKS) once."""
    import httpx
    from redis.asyncio import from_url

    settings: Settings = get_settings()
    kc_url = settings.keycloak_url
    realm = settings.keycloak_realm
    provider = build_jwks_provider(
        keycloak_url=kc_url,
        realm=realm,
        http_client=httpx.AsyncClient(timeout=10.0),
        cache=RedisCache(from_url(settings.redis_url)),
    )
    assert kc_url and realm  # build_jwks_provider raised otherwise  # noqa: S101
    return TokenValidator(
        provider,
        audience=settings.keycloak_audience,
        issuer=issuer_for(kc_url, realm),
    )


def get_token_validator() -> TokenValidator:
    return _default_validator()


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    validator: TokenValidator = Depends(get_token_validator),
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await validator.validate(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await upsert_user(session, principal)
    await session.commit()
    return user


def require_role(
    *roles: str,
) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """Dependency factory: 403 unless the caller holds one of ``roles``."""

    async def checker(principal: Principal = Depends(get_principal)) -> Principal:
        if not any(principal.has_role(r) for r in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {' or '.join(roles)}",
            )
        return principal

    return checker

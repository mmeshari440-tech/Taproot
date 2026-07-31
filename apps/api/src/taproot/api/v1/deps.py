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
from taproot.core.events import EventBus, RedisEventBus
from taproot.core.exceptions import AuthenticationError, ConfigurationError
from taproot.core.secrets import SecretStore, build_secret_store
from taproot.core.security import (
    Principal,
    TokenValidator,
    build_jwks_provider,
    issuer_for,
)
from taproot.db.models import User
from taproot.db.session import get_session
from taproot.integrations.gitlab import GitLabClient
from taproot.services.user_service import upsert_user
from taproot.workers.queue import ArqJobQueue, JobQueue

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
        cache=RedisCache(from_url(settings.redis_url)),  # type: ignore[no-untyped-call]
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


@lru_cache
def get_secret_store() -> SecretStore:
    settings = get_settings()
    vault_client = None
    if settings.secret_store == "vault":  # noqa: S105  (store kind, not a secret)
        import hvac

        vault_client = hvac.Client(url=settings.vault_addr, token=settings.vault_token)
    return build_secret_store(
        kind=settings.secret_store,
        env=settings.env,
        local_secret_key=settings.local_secret_key,
        vault_client=vault_client,
    )


@lru_cache
def get_http_client() -> Any:
    import httpx

    return httpx.AsyncClient(timeout=30.0)


_redis_pubsub_client: Any = None


def get_event_bus() -> EventBus:
    """Redis-backed SSE fan-out bus, sharing one client."""
    global _redis_pubsub_client
    if _redis_pubsub_client is None:
        from redis.asyncio import from_url

        _redis_pubsub_client = from_url(get_settings().redis_url)  # type: ignore[no-untyped-call]
    return RedisEventBus(_redis_pubsub_client)


_arq_pool: Any = None


async def get_job_queue() -> JobQueue:
    """ARQ-backed job queue, sharing one lazily-created pool."""
    global _arq_pool
    if _arq_pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return ArqJobQueue(_arq_pool)


def get_gitlab_client() -> GitLabClient:
    settings = get_settings()
    if not settings.gitlab_url or not settings.gitlab_token:
        raise ConfigurationError(
            "GitLab discovery requires TAPROOT_GITLAB_URL and TAPROOT_GITLAB_TOKEN."
        )
    return GitLabClient(
        base_url=settings.gitlab_url,
        token=settings.gitlab_token,
        http_client=get_http_client(),
    )


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


require_admin = require_role("platform-admin")


async def get_current_admin(
    _: Principal = Depends(require_admin),
    user: User = Depends(get_current_user),
) -> User:
    """Enforce platform-admin and return the (upserted) acting user for audit."""
    return user

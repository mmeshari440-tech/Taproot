"""JWT authentication primitives (ARCHITECTURE.md §8.1).

* JWKS fetched from Keycloak and cached (Redis in prod), refetched on an unknown
  ``kid``.
* Tokens validated for signature, ``iss``, ``aud`` and ``exp``.
* Roles come from the token (``realm_access.roles``) — never the database.

Pure validation lives here (no DB). User upsert for audit joins is a service
concern, invoked from the API dependency layer.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, cast, runtime_checkable

from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError
from pydantic import BaseModel

from taproot.core.cache import AsyncCache
from taproot.core.exceptions import AuthenticationError, ConfigurationError

JWKS_CACHE_KEY = "auth:jwks"
JWKS_TTL_SECONDS = 3600  # 1h (ARCHITECTURE.md §8.1)
DEFAULT_ALGORITHMS = ["RS256"]


class Principal(BaseModel):
    """The authenticated caller, derived from a validated access token."""

    sub: str
    email: str | None = None
    name: str | None = None
    roles: list[str] = []

    def has_role(self, role: str) -> bool:
        return role in self.roles


@runtime_checkable
class JwksProvider(Protocol):
    async def get_key(self, kid: str) -> dict[str, Any] | None:
        """Return the JWK for ``kid``, or ``None`` if it cannot be found."""
        ...


def _find_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return cast("dict[str, Any]", key)
    return None


class StaticJwksProvider:
    """A fixed JWKS — useful offline and in tests."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        self._jwks = jwks

    async def get_key(self, kid: str) -> dict[str, Any] | None:
        return _find_key(self._jwks, kid)


class RemoteJwksProvider:
    """Fetches a realm's JWKS over HTTP and caches it; refetches on unknown ``kid``.

    ``http_client`` is an ``httpx.AsyncClient``-compatible object.
    """

    def __init__(self, certs_url: str, http_client: Any, cache: AsyncCache) -> None:
        self._certs_url = certs_url
        self._http = http_client
        self._cache = cache

    async def _fetch(self) -> dict[str, Any]:
        resp = await self._http.get(self._certs_url)
        resp.raise_for_status()
        return dict(resp.json())

    async def get_key(self, kid: str) -> dict[str, Any] | None:
        cached = await self._cache.get(JWKS_CACHE_KEY)
        if cached is not None:
            found = _find_key(json.loads(cached), kid)
            if found is not None:
                return found
            # Unknown kid: keys may have rotated — refetch once before giving up.
        jwks = await self._fetch()
        await self._cache.set(JWKS_CACHE_KEY, json.dumps(jwks), JWKS_TTL_SECONDS)
        return _find_key(jwks, kid)


class TokenValidator:
    """Validates access tokens against a realm's signing keys and claims."""

    def __init__(
        self,
        jwks_provider: JwksProvider,
        audience: str,
        issuer: str,
        algorithms: list[str] | None = None,
    ) -> None:
        self._jwks = jwks_provider
        self._audience = audience
        self._issuer = issuer
        self._algorithms = algorithms or DEFAULT_ALGORITHMS

    async def validate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise AuthenticationError("Malformed token header") from exc

        kid = header.get("kid")
        if not kid:
            raise AuthenticationError("Token header missing 'kid'")

        key = await self._jwks.get_key(kid)
        if key is None:
            raise AuthenticationError("Unknown token signing key")

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
            )
        except ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired") from exc
        except JWTClaimsError as exc:
            raise AuthenticationError(f"Invalid token claims: {exc}") from exc
        except JWTError as exc:
            raise AuthenticationError("Invalid token signature") from exc

        realm_access = claims.get("realm_access") or {}
        return Principal(
            sub=str(claims["sub"]),
            email=claims.get("email"),
            name=claims.get("name") or claims.get("preferred_username"),
            roles=list(realm_access.get("roles", [])),
        )


def build_jwks_provider(
    *, keycloak_url: str | None, realm: str | None, http_client: Any, cache: AsyncCache
) -> RemoteJwksProvider:
    if not keycloak_url or not realm:
        raise ConfigurationError(
            "Auth requires TAPROOT_KEYCLOAK_URL and TAPROOT_KEYCLOAK_REALM."
        )
    certs_url = f"{keycloak_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/certs"
    return RemoteJwksProvider(certs_url=certs_url, http_client=http_client, cache=cache)


def issuer_for(keycloak_url: str, realm: str) -> str:
    return f"{keycloak_url.rstrip('/')}/realms/{realm}"

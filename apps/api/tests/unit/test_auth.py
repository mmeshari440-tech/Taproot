from __future__ import annotations

import base64
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from taproot.api.v1.deps import get_token_validator
from taproot.core.exceptions import AuthenticationError
from taproot.core.security import StaticJwksProvider, TokenValidator
from taproot.db.base import Base
from taproot.db.models import User
from taproot.db.session import get_session
from taproot.main import create_app

ISSUER = "http://kc.local/realms/taproot"
AUDIENCE = "taproot-api"
KID = "test-key"


# --- key + token helpers ----------------------------------------------------
def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_key() -> tuple[str, dict[str, Any]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    nums = key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": _b64url_uint(nums.n),
                "e": _b64url_uint(nums.e),
            }
        ]
    }
    return pem, jwks


PRIVATE_PEM, JWKS = _make_key()


def make_token(
    *,
    sub: str = "user-1",
    aud: str = AUDIENCE,
    iss: str = ISSUER,
    roles: list[str] | None = None,
    email: str = "u@x.co",
    name: str = "User One",
    exp_delta: int = 3600,
    kid: str = KID,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + exp_delta,
        "realm_access": {"roles": roles or []},
    }
    return jwt.encode(claims, PRIVATE_PEM, algorithm="RS256", headers={"kid": kid})


def make_validator() -> TokenValidator:
    return TokenValidator(StaticJwksProvider(JWKS), audience=AUDIENCE, issuer=ISSUER)


# --- validator unit tests ---------------------------------------------------
async def test_valid_token_yields_principal() -> None:
    principal = await make_validator().validate(make_token(roles=["tech-user"]))
    assert principal.sub == "user-1"
    assert principal.email == "u@x.co"
    assert principal.has_role("tech-user")


async def test_expired_token_rejected() -> None:
    with pytest.raises(AuthenticationError):
        await make_validator().validate(make_token(exp_delta=-10))


async def test_wrong_audience_rejected() -> None:
    with pytest.raises(AuthenticationError):
        await make_validator().validate(make_token(aud="someone-else"))


async def test_wrong_issuer_rejected() -> None:
    with pytest.raises(AuthenticationError):
        await make_validator().validate(make_token(iss="http://evil/realms/x"))


async def test_unknown_kid_rejected() -> None:
    with pytest.raises(AuthenticationError):
        await make_validator().validate(make_token(kid="not-in-jwks"))


async def test_malformed_token_rejected() -> None:
    with pytest.raises(AuthenticationError):
        await make_validator().validate("not-a-jwt")


# --- app-level RBAC ---------------------------------------------------------
@pytest_asyncio.fixture
async def client() -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[Any]]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session_override() -> AsyncIterator[Any]:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_token_validator] = make_validator
    app.dependency_overrides[get_session] = _session_override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, maker
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_me_requires_token(client: tuple[AsyncClient, Any]) -> None:
    c, _ = client
    resp = await c.get("/api/v1/me")
    assert resp.status_code == 401


async def test_me_rejects_expired(client: tuple[AsyncClient, Any]) -> None:
    c, _ = client
    resp = await c.get("/api/v1/me", headers=_auth(make_token(exp_delta=-5)))
    assert resp.status_code == 401


async def test_me_returns_user_and_upserts_once(
    client: tuple[AsyncClient, async_sessionmaker[Any]],
) -> None:
    c, maker = client
    token = make_token(sub="abc", roles=["tech-user"], email="a@b.co")
    r1 = await c.get("/api/v1/me", headers=_auth(token))
    r2 = await c.get("/api/v1/me", headers=_auth(token))
    assert r1.status_code == 200
    body = r1.json()
    assert body["sub"] == "abc"
    assert body["roles"] == ["tech-user"]
    assert r2.status_code == 200

    async with maker() as session:
        count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    assert count == 1  # upserted, not duplicated


async def test_admin_ping_allows_admin(client: tuple[AsyncClient, Any]) -> None:
    c, _ = client
    resp = await c.get("/api/v1/admin/ping", headers=_auth(make_token(roles=["platform-admin"])))
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_admin_ping_forbids_tech_user(client: tuple[AsyncClient, Any]) -> None:
    c, _ = client
    resp = await c.get("/api/v1/admin/ping", headers=_auth(make_token(roles=["tech-user"])))
    assert resp.status_code == 403

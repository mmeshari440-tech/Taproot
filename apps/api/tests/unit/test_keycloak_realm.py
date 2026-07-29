"""Structural checks on the Keycloak realm export (T-06).

Cheap guardrails so an accidental edit to the realm can't silently drop a role,
break PKCE, or lose the API audience mapper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REALM_PATH = Path(__file__).resolve().parents[4] / "infra" / "keycloak" / "realm-export.json"


@pytest.fixture(scope="module")
def realm() -> dict:
    return json.loads(REALM_PATH.read_text())


def test_realm_name(realm: dict) -> None:
    assert realm["realm"] == "taproot"
    assert realm["enabled"] is True


def test_realm_roles(realm: dict) -> None:
    names = {r["name"] for r in realm["roles"]["realm"]}
    assert {"platform-admin", "tech-user"} <= names


def test_web_client_is_public_pkce(realm: dict) -> None:
    web = next(c for c in realm["clients"] if c["clientId"] == "taproot-web")
    assert web["publicClient"] is True
    assert web["standardFlowEnabled"] is True
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"


def test_web_client_adds_api_audience(realm: dict) -> None:
    web = next(c for c in realm["clients"] if c["clientId"] == "taproot-web")
    mappers = web.get("protocolMappers", [])
    audience = next(m for m in mappers if m["protocolMapper"] == "oidc-audience-mapper")
    assert audience["config"]["included.client.audience"] == "taproot-api"
    assert audience["config"]["access.token.claim"] == "true"


def test_api_client_exists(realm: dict) -> None:
    api = next(c for c in realm["clients"] if c["clientId"] == "taproot-api")
    assert api["bearerOnly"] is True


def test_two_users_one_per_role(realm: dict) -> None:
    assigned = {u["username"]: set(u["realmRoles"]) for u in realm["users"]}
    assert len(assigned) == 2
    covered: set[str] = set().union(*assigned.values())
    assert {"platform-admin", "tech-user"} <= covered

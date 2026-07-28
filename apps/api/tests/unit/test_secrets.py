from __future__ import annotations

from typing import Any

import pytest

from taproot.core.exceptions import SecretStoreError
from taproot.core.secrets import (
    LocalEncryptedSecretStore,
    VaultSecretStore,
    build_secret_store,
)


# --- Local store ------------------------------------------------------------
async def test_local_roundtrip() -> None:
    store = LocalEncryptedSecretStore(key="k", env="development")
    ref = await store.store("elastic", "s3cr3t-token")
    assert ref.startswith("local://")
    assert await store.retrieve(ref) == "s3cr3t-token"


async def test_local_encrypts_at_rest() -> None:
    store = LocalEncryptedSecretStore(key="k")
    ref = await store.store("sentry", "plaintext-value")
    blob = store._blobs[ref.removeprefix("local://")]  # type: ignore[attr-defined]
    assert b"plaintext-value" not in blob


async def test_local_rotate() -> None:
    store = LocalEncryptedSecretStore(key="k")
    ref = await store.store("appd", "old")
    same_ref = await store.rotate(ref, "new")
    assert same_ref == ref
    assert await store.retrieve(ref) == "new"


async def test_local_delete() -> None:
    store = LocalEncryptedSecretStore(key="k")
    ref = await store.store("x", "v")
    await store.delete(ref)
    with pytest.raises(SecretStoreError):
        await store.retrieve(ref)


def test_local_refuses_production() -> None:
    with pytest.raises(SecretStoreError):
        LocalEncryptedSecretStore(key="k", env="production")


# --- Vault store (fake injected client) -------------------------------------
class _FakeKvV2:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, str]] = {}

    def create_or_update_secret(
        self, path: str, secret: dict[str, str], mount_point: str
    ) -> None:
        self.data[path] = dict(secret)

    def read_secret_version(self, path: str, mount_point: str) -> dict[str, Any]:
        return {"data": {"data": self.data[path]}}

    def delete_metadata_and_all_versions(self, path: str, mount_point: str) -> None:
        self.data.pop(path, None)


class _FakeVaultClient:
    def __init__(self) -> None:
        self.secrets = type("S", (), {"kv": type("K", (), {"v2": _FakeKvV2()})()})()


async def test_vault_roundtrip_rotate_delete() -> None:
    store = VaultSecretStore(client=_FakeVaultClient())
    ref = await store.store("elastic", "vault-secret")
    assert ref.startswith("vault://")
    assert await store.retrieve(ref) == "vault-secret"
    await store.rotate(ref, "vault-secret-2")
    assert await store.retrieve(ref) == "vault-secret-2"
    await store.delete(ref)


# --- Factory ----------------------------------------------------------------
def test_factory_local_requires_key() -> None:
    with pytest.raises(SecretStoreError):
        build_secret_store(kind="local", env="development", local_secret_key=None)


def test_factory_builds_local() -> None:
    store = build_secret_store(kind="local", env="development", local_secret_key="k")
    assert isinstance(store, LocalEncryptedSecretStore)


def test_factory_unknown_kind() -> None:
    with pytest.raises(SecretStoreError):
        build_secret_store(kind="s3", env="development")

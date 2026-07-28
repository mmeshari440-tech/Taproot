"""Secret storage (ARCHITECTURE.md §8.2).

Integration tokens go to a :class:`SecretStore`; Postgres stores only a
``secret_ref``. Tokens are never returned by an API, never logged, never put in
a step payload, and never sent to the LLM.

Two implementations:

* :class:`VaultSecretStore` — production. Wraps an ``hvac``-style KV v2 client
  which is injected, so it is testable without a live Vault.
* :class:`LocalEncryptedSecretStore` — dev only. Envelope-style encryption with
  a dev key; refuses to start when ``TAPROOT_ENV=production``.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from cryptography.fernet import Fernet

from taproot.core.exceptions import SecretStoreError


@runtime_checkable
class SecretStore(Protocol):
    """Contract every secret store implements. All methods are async so the
    same interface works for network-backed stores."""

    async def store(self, name: str, secret: str) -> str:
        """Persist ``secret`` and return an opaque ``secret_ref``."""
        ...

    async def retrieve(self, ref: str) -> str:
        """Return the secret referenced by ``ref``."""
        ...

    async def rotate(self, ref: str, secret: str) -> str:
        """Replace the value at ``ref`` and return the (possibly new) ref."""
        ...

    async def delete(self, ref: str) -> None:
        """Remove the secret referenced by ``ref``."""
        ...


def _fernet_from_key(key: str) -> Fernet:
    """Derive a valid Fernet key from an arbitrary dev string."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class LocalEncryptedSecretStore:
    """Dev-only encrypted store.

    Values are Fernet-encrypted with a key derived from ``TAPROOT_LOCAL_SECRET_KEY``.
    Storage is in-process and therefore ephemeral — acceptable for local
    development; production uses :class:`VaultSecretStore`.
    """

    scheme = "local://"

    def __init__(self, key: str, env: str = "development") -> None:
        if env == "production":
            raise SecretStoreError(
                "LocalEncryptedSecretStore must not run in production; "
                "set TAPROOT_SECRET_STORE=vault."
            )
        if not key:
            raise SecretStoreError("TAPROOT_LOCAL_SECRET_KEY is required for the local store.")
        self._fernet = _fernet_from_key(key)
        self._blobs: dict[str, bytes] = {}

    async def store(self, name: str, secret: str) -> str:
        ref_id = uuid4().hex
        self._blobs[ref_id] = self._fernet.encrypt(secret.encode("utf-8"))
        return f"{self.scheme}{ref_id}"

    async def retrieve(self, ref: str) -> str:
        ref_id = ref.removeprefix(self.scheme)
        try:
            return self._fernet.decrypt(self._blobs[ref_id]).decode("utf-8")
        except KeyError as exc:
            raise SecretStoreError(f"Unknown secret ref: {ref}") from exc

    async def rotate(self, ref: str, secret: str) -> str:
        ref_id = ref.removeprefix(self.scheme)
        if ref_id not in self._blobs:
            raise SecretStoreError(f"Unknown secret ref: {ref}")
        self._blobs[ref_id] = self._fernet.encrypt(secret.encode("utf-8"))
        return ref

    async def delete(self, ref: str) -> None:
        self._blobs.pop(ref.removeprefix(self.scheme), None)


class VaultSecretStore:
    """HashiCorp Vault KV v2 store.

    The ``client`` is an ``hvac.Client``-compatible object, injected so the store
    is unit-testable without a live Vault.

    # TODO: verify against a live Vault instance (mount point, path policy).
    """

    scheme = "vault://"

    def __init__(
        self, client: Any, mount_point: str = "secret", base_path: str = "taproot"
    ) -> None:
        self._client = client
        self._mount_point = mount_point
        self._base_path = base_path.strip("/")

    def _path_for(self, ref: str) -> str:
        return ref.removeprefix(self.scheme)

    async def store(self, name: str, secret: str) -> str:
        path = f"{self._base_path}/{name}/{uuid4().hex}"
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path, secret={"value": secret}, mount_point=self._mount_point
        )
        return f"{self.scheme}{path}"

    async def retrieve(self, ref: str) -> str:
        resp = self._client.secrets.kv.v2.read_secret_version(
            path=self._path_for(ref), mount_point=self._mount_point
        )
        try:
            return str(resp["data"]["data"]["value"])
        except (KeyError, TypeError) as exc:
            raise SecretStoreError(f"Malformed Vault response for ref: {ref}") from exc

    async def rotate(self, ref: str, secret: str) -> str:
        self._client.secrets.kv.v2.create_or_update_secret(
            path=self._path_for(ref), secret={"value": secret}, mount_point=self._mount_point
        )
        return ref

    async def delete(self, ref: str) -> None:
        self._client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=self._path_for(ref), mount_point=self._mount_point
        )


def build_secret_store(
    *,
    kind: str,
    env: str,
    local_secret_key: str | None = None,
    vault_client: Any | None = None,
) -> SecretStore:
    """Factory selecting the configured store. Raises a clear
    :class:`SecretStoreError` when the choice is not usable."""
    if kind == "local":
        if not local_secret_key:
            raise SecretStoreError("secret_store=local requires TAPROOT_LOCAL_SECRET_KEY.")
        return LocalEncryptedSecretStore(key=local_secret_key, env=env)
    if kind == "vault":
        if vault_client is None:
            raise SecretStoreError("secret_store=vault requires a configured Vault client.")
        return VaultSecretStore(client=vault_client)
    raise SecretStoreError(f"Unknown secret store: {kind!r}")

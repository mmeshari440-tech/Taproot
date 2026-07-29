from __future__ import annotations

from typing import Any

from taproot.core.cache import InMemoryCache
from taproot.core.security import RemoteJwksProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttp:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = payloads
        self.calls = 0

    async def get(self, url: str) -> _FakeResponse:
        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        return _FakeResponse(payload)


def _jwks(*kids: str) -> dict[str, Any]:
    return {"keys": [{"kid": k, "kty": "RSA"} for k in kids]}


async def test_fetches_then_serves_from_cache() -> None:
    http = _FakeHttp([_jwks("k1")])
    provider = RemoteJwksProvider("http://kc/certs", http, InMemoryCache())

    first = await provider.get_key("k1")
    second = await provider.get_key("k1")

    assert first is not None and second is not None
    assert http.calls == 1  # second call served from cache


async def test_refetches_on_unknown_kid() -> None:
    # First response only has k1; after rotation the endpoint serves k1 + k2.
    http = _FakeHttp([_jwks("k1"), _jwks("k1", "k2")])
    provider = RemoteJwksProvider("http://kc/certs", http, InMemoryCache())

    await provider.get_key("k1")  # populates cache (call 1)
    key = await provider.get_key("k2")  # unknown kid → refetch (call 2)

    assert key is not None
    assert http.calls == 2


async def test_unknown_kid_after_refetch_returns_none() -> None:
    http = _FakeHttp([_jwks("k1")])
    provider = RemoteJwksProvider("http://kc/certs", http, InMemoryCache())

    assert await provider.get_key("missing") is None
    assert http.calls == 1

from __future__ import annotations

import httpx

from taproot.integrations.appdynamics import AppDynamicsClient


def _client(handler: object) -> AppDynamicsClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport, base_url="https://appd.test")
    return AppDynamicsClient(
        "https://appd.test", "cid", "secret", "99", http_client=http, backoff_base=0.0
    )


async def test_token_is_cached_across_calls() -> None:
    counts = {"oauth": 0, "bt": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/access_token"):
            counts["oauth"] += 1
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 300})
        counts["bt"] += 1
        return httpx.Response(200, json=[{"id": 1, "name": "checkout", "tierName": "web"}])

    client = _client(handler)
    await client.business_transactions()
    bts = await client.business_transactions()

    assert counts["oauth"] == 1  # token reused
    assert counts["bt"] == 2
    assert bts[0].name == "checkout"
    assert bts[0].tier_name == "web"


async def test_error_snapshots_extracts_exit_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 300})
        return httpx.Response(
            200,
            json=[
                {
                    "requestGUID": "g1",
                    "errorDetails": "SocketTimeout",
                    "exitCalls": [
                        {"target": "partner-api", "exitPointType": "HTTP", "errorCount": 3}
                    ],
                }
            ],
        )

    snaps = await _client(handler).error_snapshots()
    assert snaps[0].error_message == "SocketTimeout"
    assert snaps[0].exit_calls[0].target == "partner-api"
    assert snaps[0].exit_calls[0].error_count == 3

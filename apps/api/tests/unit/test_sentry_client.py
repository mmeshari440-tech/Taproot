from __future__ import annotations

import httpx

from taproot.integrations.sentry import SentryClient


def _client(handler: object) -> SentryClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport, base_url="https://sentry.test")
    return SentryClient(
        "https://sentry.test", "tok", "acme", "payments", http_client=http, backoff_base=0.0
    )


async def test_search_issues_normalizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/0/projects/acme/payments/issues/"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "42",
                    "title": "NullPointerException",
                    "culprit": "svc.payment",
                    "level": "error",
                    "count": "17",
                    "userCount": 5,
                    "firstSeen": "2026-07-01T00:00:00Z",
                    "lastSeen": "2026-07-31T00:00:00Z",
                }
            ],
        )

    issues = await _client(handler).search_issues("is:unresolved NPE")
    assert issues[0].id == "42"
    assert issues[0].count == 17
    assert issues[0].user_count == 5


async def test_latest_event_extracts_frames_and_release() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "evt1",
                "release": {"version": "a1b2c3d"},
                "tags": [{"key": "environment", "value": "prod"}],
                "entries": [
                    {
                        "type": "exception",
                        "data": {
                            "values": [
                                {
                                    "stacktrace": {
                                        "frames": [
                                            {
                                                "filename": "payment.py",
                                                "function": "charge",
                                                "lineNo": 142,
                                                "inApp": True,
                                            },
                                            {"filename": "lib.py", "function": "x", "inApp": False},
                                        ]
                                    }
                                }
                            ]
                        },
                    }
                ],
            },
        )

    event = await _client(handler).latest_event("42")
    assert event.release == "a1b2c3d"
    assert event.tags["environment"] == "prod"
    in_app = [f for f in event.frames if f.in_app]
    assert len(in_app) == 1
    assert in_app[0].lineno == 142


async def test_issue_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"key": "browser", "topValues": [{"value": "Chrome"}, {"value": "Firefox"}]}],
        )

    tags = await _client(handler).issue_tags("42")
    assert tags["browser"] == ["Chrome", "Firefox"]

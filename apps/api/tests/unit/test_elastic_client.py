from __future__ import annotations

import httpx

from taproot.integrations.elastic import ElasticClient


def _client(handler: object) -> ElasticClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport, base_url="https://es.test")
    return ElasticClient("https://es.test", "apikey", "logs-*", http_client=http, backoff_base=0.0)


async def test_search_parses_docs_and_txn_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/logs-*/_search"
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "message": "boom",
                                "severity": "ERROR",
                                "transaction_id": "t1",
                            }
                        }
                    ]
                },
                "aggregations": {"txns": {"buckets": [{"key": "t1"}, {"key": "t2"}]}},
            },
        )

    result = await _client(handler).search("NullPointer")
    assert result.docs[0].message == "boom"
    assert result.transaction_ids == ["t1", "t2"]


async def test_thread_parses_nested_service_and_timestamp() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-07-31T10:00:00Z",
                                "severity": "INFO",
                                "message": "partner-api 200 empty body",
                                "service": {"name": "payments"},
                            }
                        },
                        {
                            "_source": {
                                "@timestamp": "2026-07-31T10:00:01Z",
                                "severity": "ERROR",
                                "message": "NPE",
                            }
                        },
                    ]
                }
            },
        )

    docs = await _client(handler).thread("t1")
    assert [d.severity for d in docs] == ["INFO", "ERROR"]
    assert docs[0].service == "payments"
    assert docs[0].timestamp is not None and docs[0].timestamp.year == 2026


async def test_histogram_returns_day_buckets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "aggregations": {
                    "per_day": {
                        "buckets": [
                            {"key_as_string": "2026-07-30T00:00:00.000Z", "doc_count": 3},
                            {"key_as_string": "2026-07-31T00:00:00.000Z", "doc_count": 5},
                        ]
                    }
                }
            },
        )

    buckets = await _client(handler).histogram("NullPointer")
    assert [(b.date, b.count) for b in buckets] == [("2026-07-30", 3), ("2026-07-31", 5)]


async def test_thread_prefers_container_name_for_service() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {"_source": {"container.name": "payments-be", "message": "x"}},
                        {"_source": {"container": {"name": "payments-fe"}, "message": "y"}},
                    ]
                }
            },
        )

    docs = await _client(handler).thread("t1")
    assert [d.service for d in docs] == ["payments-be", "payments-fe"]


async def test_cardinality_returns_int() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"aggregations": {"distinct": {"value": 42}}})

    assert await _client(handler).cardinality("NullPointer") == 42


async def test_retries_on_5xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"aggregations": {"distinct": {"value": 1}}})

    assert await _client(handler).cardinality("x") == 1
    assert calls["n"] == 2

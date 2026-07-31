"""Elasticsearch integration — connection test (T-11) + typed client (T-13).

`ElasticClient` implements `search`/`thread`/`histogram`/`cardinality` per
PLAN.md §6.3, returning normalized `LogDoc` models (never raw ES JSON). Field
mapping assumes the documented ELK schema (`@timestamp`, `service.name`, …);
`# TODO: verify against live instance` (ARCHITECTURE.md §12, open questions 1-4).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from taproot.core.logging import get_logger
from taproot.core.models import (
    BroadSearchResult,
    ConnectionTestResult,
    DayBucket,
    LogDoc,
)
from taproot.integrations._http import send_with_retries, short_error

_log = get_logger(__name__)

ERROR_SEVERITIES = ["ERROR", "FATAL", "CRITICAL"]


async def test_connection(
    *,
    base_url: str,
    external_id: str | None,
    config: dict[str, Any],
    token: str | None,
    http_client: httpx.AsyncClient,
) -> ConnectionTestResult:
    """`external_id` is the index pattern (e.g. ``logs-*``)."""
    index = external_id or "*"
    url = f"{base_url.rstrip('/')}/{index}/_search"
    headers = {"Authorization": f"ApiKey {token}"} if token else {}
    start = time.perf_counter()
    try:
        resp = await http_client.post(
            url, params={"size": 0}, json={"query": {"match_all": {}}}, headers=headers
        )
    except httpx.HTTPError as exc:
        return ConnectionTestResult(ok=False, latency_ms=0, error=str(exc))
    latency = int((time.perf_counter() - start) * 1000)
    if resp.status_code == 200:
        hits = resp.json().get("hits", {}).get("total", {})
        return ConnectionTestResult(
            ok=True, latency_ms=latency, detail=f"index '{index}' reachable ({hits})"
        )
    return ConnectionTestResult(ok=False, latency_ms=latency, error=short_error(resp))


def _parse_doc(source: dict[str, Any]) -> LogDoc:
    """Map an ES ``_source`` to a LogDoc, tolerating nested or flat fields."""
    service = source.get("service.name")
    if service is None and isinstance(source.get("service"), dict):
        service = source["service"].get("name")

    raw_ts = source.get("@timestamp")
    timestamp: datetime | None = None
    if isinstance(raw_ts, str):
        try:
            timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            timestamp = None

    status = source.get("http.status_code")
    if status is None and isinstance(source.get("http"), dict):
        status = source["http"].get("status_code")

    url = source.get("url.full")
    if url is None and isinstance(source.get("url"), dict):
        url = source["url"].get("full")

    http_status = int(status) if isinstance(status, int | str) and str(status).isdigit() else None

    return LogDoc(
        timestamp=timestamp,
        severity=source.get("severity"),
        message=source.get("message"),
        stack_trace=source.get("stack_trace"),
        service=service,
        transaction_id=source.get("transaction_id"),
        user_name=source.get("user_name"),
        http_status=http_status,
        url=url,
    )


class ElasticClient:
    """Read-only Elasticsearch client (T-13).

    `http_client` is injectable for tests. Retries on 5xx/429 only; 30s timeout.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None,
        index_pattern: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._index = index_pattern
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._headers = {"Authorization": f"ApiKey {token}"} if token else {}
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def _search(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/{self._index}/_search"
        start = time.perf_counter()
        resp = await send_with_retries(
            lambda: self._http.post(url, json=body, headers=self._headers),
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
        )
        _log.info(
            "elastic_call",
            provider="elastic",
            method="_search",
            status=resp.status_code,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        resp.raise_for_status()
        return dict(resp.json())

    async def search(
        self, error_text: str, *, window_days: int = 7, size: int = 50
    ) -> BroadSearchResult:
        """Broad search: fuzzy match on message/stack_trace, error severities,
        window filter, plus a distinct-transaction aggregation (PLAN.md §6.3)."""
        body = {
            "size": size,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": error_text,
                                "fields": ["message^3", "stack_trace"],
                                "type": "best_fields",
                                "fuzziness": "AUTO",
                            }
                        }
                    ],
                    "filter": [
                        {"terms": {"severity": ERROR_SEVERITIES}},
                        {"range": {"@timestamp": {"gte": f"now-{window_days}d"}}},
                    ],
                }
            },
            "aggs": {"txns": {"terms": {"field": "transaction_id", "size": 20}}},
            "sort": [{"@timestamp": "desc"}],
        }
        data = await self._search(body)
        docs = [_parse_doc(h.get("_source", {})) for h in data.get("hits", {}).get("hits", [])]
        buckets = data.get("aggregations", {}).get("txns", {}).get("buckets", [])
        txn_ids = [str(b["key"]) for b in buckets if b.get("key") is not None]
        return BroadSearchResult(docs=docs, transaction_ids=txn_ids)

    async def thread(self, transaction_id: str, *, size: int = 500) -> list[LogDoc]:
        """All docs for a transaction, ALL severities, @timestamp ASC (the deep
        dive input for thread_walk, T-21)."""
        body = {
            "size": size,
            "query": {"bool": {"filter": [{"term": {"transaction_id": transaction_id}}]}},
            "sort": [{"@timestamp": "asc"}],
            "_source": [
                "@timestamp",
                "severity",
                "message",
                "stack_trace",
                "service.name",
                "user_name",
                "transaction_id",
            ],
        }
        data = await self._search(body)
        return [_parse_doc(h.get("_source", {})) for h in data.get("hits", {}).get("hits", [])]

    async def histogram(self, signature: str, *, window_days: int = 7) -> list[DayBucket]:
        """Daily counts of the error signature over the window (req 12.1)."""
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "must": [{"match_phrase": {"message": signature}}],
                    "filter": [{"range": {"@timestamp": {"gte": f"now-{window_days}d"}}}],
                }
            },
            "aggs": {
                "per_day": {
                    "date_histogram": {"field": "@timestamp", "calendar_interval": "1d"}
                }
            },
        }
        data = await self._search(body)
        buckets = data.get("aggregations", {}).get("per_day", {}).get("buckets", [])
        return [
            DayBucket(date=str(b.get("key_as_string", ""))[:10], count=int(b.get("doc_count", 0)))
            for b in buckets
        ]

    async def cardinality(
        self, signature: str, *, window_days: int = 7, field: str = "user_name"
    ) -> int:
        """Distinct count of ``field`` for the signature over the window."""
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "must": [{"match_phrase": {"message": signature}}],
                    "filter": [{"range": {"@timestamp": {"gte": f"now-{window_days}d"}}}],
                }
            },
            "aggs": {"distinct": {"cardinality": {"field": field}}},
        }
        data = await self._search(body)
        return int(data.get("aggregations", {}).get("distinct", {}).get("value", 0))

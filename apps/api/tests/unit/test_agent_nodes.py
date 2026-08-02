"""Node-body tests for T-21 (normalize → broad search → select → thread_walk).

Nodes are exercised through the ``@node`` decorator (they take a state + a graph
config carrying the :class:`AgentContext`), the same way LangGraph invokes them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from taproot.agent.context import AgentContext
from taproot.agent.nodes import (
    elastic_broad_search,
    normalize_query,
    select_threads,
    third_party_probe,
    thread_walk,
)
from taproot.agent.schemas import LogThread
from taproot.agent.state import InvestigationState
from taproot.core.models import BroadSearchResult, LogDoc

_THREAD_SUMMARY_CHARS = 400 * 4  # nodes._THREAD_SUMMARY_MAX_TOKENS * ~4 chars/token


def _doc(
    sec: int,
    severity: str,
    message: str,
    service: str,
    *,
    txn: str = "txn-1",
    user: str | None = None,
) -> LogDoc:
    return LogDoc(
        timestamp=datetime(2026, 8, 1, 12, 0, sec, tzinfo=UTC),
        severity=severity,
        message=message,
        service=service,
        transaction_id=txn,
        user_name=user,
    )


class _FakeElastic:
    def __init__(
        self, search: BroadSearchResult, threads: dict[str, list[LogDoc]] | None = None
    ) -> None:
        self._search = search
        self._threads = threads or {}

    async def search(
        self, error_text: str, *, window_days: int = 7, size: int = 50
    ) -> BroadSearchResult:
        return self._search

    async def thread(self, transaction_id: str, *, size: int = 500) -> list[LogDoc]:
        return list(self._threads.get(transaction_id, []))


class _BoomElastic:
    async def search(self, *a: Any, **k: Any) -> BroadSearchResult:
        raise RuntimeError("es down")

    async def thread(self, *a: Any, **k: Any) -> list[LogDoc]:
        return []


def _ctx(clients: list[Any], *, max_threads: int = 5) -> AgentContext:
    async def emit_start(_n: str, _t: str) -> int:
        return 1

    async def emit_finish(_seq: int, _n: str, _s: str, _summary: str | None) -> None:
        return None

    return AgentContext(emit_start, emit_finish, max_threads=max_threads, elastic_clients=clients)


def _config(ctx: AgentContext) -> dict[str, Any]:
    return {"configurable": {"ctx": ctx}}


def _state(**kw: Any) -> InvestigationState:
    base: dict[str, Any] = {
        "investigation_id": uuid4(),
        "project_id": uuid4(),
        "error_text": "NullPointerException in service=PaymentService while charging",
    }
    base.update(kw)
    return InvestigationState(**base)


# --- normalize_query --------------------------------------------------------
async def test_normalize_extracts_class_tokens_hint_window() -> None:
    out = await normalize_query(_state(time_window_days=14), _config(_ctx([])))
    sig = out["signals"]
    assert sig.exception_class == "NullPointerException"
    assert "PaymentService" in sig.tokens
    assert sig.service_hint == "PaymentService"
    assert sig.time_window_days == 14
    assert sig.search_variants  # at least the class + the raw text


# --- elastic_broad_search ---------------------------------------------------
async def test_broad_search_no_clients_is_empty() -> None:
    out = await elastic_broad_search(_state(), _config(_ctx([])))
    assert out["broad_hits"] == []
    assert out["candidate_txn_ids"] == []


async def test_broad_search_merges_and_dedups_across_apps() -> None:
    result = BroadSearchResult(
        docs=[_doc(0, "ERROR", "boom", "api")], transaction_ids=["txn-1", "txn-2"]
    )
    fake = _FakeElastic(result)
    out = await elastic_broad_search(_state(), _config(_ctx([fake, fake])))
    assert out["candidate_txn_ids"] == ["txn-1", "txn-2"]  # deduped across both apps
    assert len(out["broad_hits"]) == 2  # both apps contributed hits


async def test_broad_search_aborts_only_when_all_apps_fail() -> None:
    with pytest.raises(RuntimeError, match="all Elasticsearch apps failed"):
        await elastic_broad_search(_state(), _config(_ctx([_BoomElastic()])))


async def test_broad_search_survives_partial_app_failure() -> None:
    ok = _FakeElastic(BroadSearchResult(docs=[_doc(0, "ERROR", "x", "api")], transaction_ids=["t"]))
    out = await elastic_broad_search(_state(), _config(_ctx([_BoomElastic(), ok])))
    assert out["candidate_txn_ids"] == ["t"]


# --- select_threads ---------------------------------------------------------
async def test_select_ranks_by_users_then_caps() -> None:
    docs = [
        _doc(0, "ERROR", "a", "api", txn="t1", user="u1"),
        _doc(1, "ERROR", "b", "api", txn="t2", user="u1"),
        _doc(2, "ERROR", "c", "api", txn="t2", user="u2"),  # t2: 2 distinct users
        _doc(3, "ERROR", "d", "api", txn="t3", user="u1"),
    ]
    st = _state(candidate_txn_ids=["t1", "t2", "t3"], broad_hits=docs)
    out = await select_threads(st, _config(_ctx([], max_threads=2)))
    assert out["candidate_txn_ids"][0] == "t2"  # most distinct users ranks first
    assert len(out["candidate_txn_ids"]) == 2  # capped at max_threads


# --- thread_walk ------------------------------------------------------------
async def test_thread_walk_orders_chronologically_and_splits() -> None:
    jumbled = [
        _doc(3, "INFO", "cleanup", "api"),
        _doc(0, "INFO", "request received", "api"),
        _doc(2, "ERROR", "NullPointerException", "api", user="alice"),
        _doc(1, "DEBUG", "calling db", "api"),
    ]
    fake = _FakeElastic(BroadSearchResult(), {"txn-1": jumbled})
    out = await thread_walk(_state(candidate_txn_ids=["txn-1"]), _config(_ctx([fake])))

    thread = out["threads"][0]
    assert [d.message for d in thread.docs] == [
        "request received",
        "calling db",
        "NullPointerException",
        "cleanup",
    ]
    assert thread.error_index == 2  # the ERROR line
    assert thread.docs[: thread.error_index]  # preamble non-empty
    assert thread.user_hash and thread.user_hash.startswith("user_")
    assert thread.summary and len(thread.summary) <= _THREAD_SUMMARY_CHARS


async def test_thread_walk_populates_services_across_boundaries() -> None:
    docs = [_doc(0, "INFO", "edge", "web"), _doc(1, "ERROR", "boom", "api")]
    fake = _FakeElastic(BroadSearchResult(), {"txn-1": docs})
    out = await thread_walk(_state(candidate_txn_ids=["txn-1"]), _config(_ctx([fake])))
    assert out["threads"][0].services == ["web", "api"]


async def test_thread_walk_skips_transactions_with_no_docs() -> None:
    fake = _FakeElastic(BroadSearchResult(), {"txn-1": [_doc(0, "ERROR", "x", "api")]})
    out = await thread_walk(_state(candidate_txn_ids=["txn-1", "missing"]), _config(_ctx([fake])))
    assert [t.txn_id for t in out["threads"]] == ["txn-1"]


# --- third_party_probe (T-23) ----------------------------------------------
def _thread(docs: list[LogDoc], error_index: int | None) -> LogThread:
    return LogThread(txn_id="txn-1", docs=docs, error_index=error_index)


async def _probe(docs: list[LogDoc], error_index: int | None = None) -> Any:
    st = _state(threads=[_thread(docs, error_index)])
    out = await third_party_probe(st, _config(_ctx([])))
    return out["third_party"]


async def test_third_party_gateway_timeout() -> None:
    docs = [
        _doc(0, "INFO", "calling https://api.partner.com/charge", "api"),
        _doc(1, "ERROR", "504 Gateway Timeout from https://api.partner.com", "api"),
    ]
    finding = await _probe(docs, error_index=1)
    assert finding.involved is True
    assert "gateway timeout" in finding.symptom.lower()
    assert finding.service == "api.partner.com"
    assert finding.had_fallback is False


async def test_third_party_connection_refused() -> None:
    docs = [_doc(0, "ERROR", "ConnectException: Connection refused to payments-gw:8443", "api")]
    finding = await _probe(docs, error_index=0)
    assert finding.involved is True
    assert "ConnectException" in finding.symptom


async def test_third_party_partner_429() -> None:
    docs = [_doc(0, "ERROR", "429 Too Many Requests from https://api.stripe.com", "api")]
    finding = await _probe(docs, error_index=0)
    assert finding.involved is True
    assert finding.service == "api.stripe.com"


async def test_third_party_http_status_field() -> None:
    # Signal comes from the normalized http_status field, not the message text.
    doc = LogDoc(severity="ERROR", message="upstream call failed", service="api", http_status=503)
    finding = await _probe([doc], error_index=0)
    assert finding.involved is True
    assert "503" in finding.symptom


async def test_internal_npe_is_not_third_party() -> None:
    docs = [
        _doc(0, "INFO", "request received", "api"),
        _doc(1, "ERROR", "java.lang.NullPointerException at com.acme.Pay.charge", "api"),
    ]
    finding = await _probe(docs, error_index=1)
    assert finding.involved is False
    assert finding.service is None


async def test_third_party_with_working_fallback() -> None:
    docs = [
        _doc(0, "ERROR", "SocketTimeoutException calling https://api.partner.com", "api"),
        _doc(1, "WARN", "circuit breaker opened; serving from cache", "api"),
        _doc(2, "INFO", "request completed", "api"),
    ]
    finding = await _probe(docs, error_index=0)
    assert finding.involved is True
    assert finding.had_fallback is True


async def test_third_party_falls_back_to_broad_hits_when_no_threads() -> None:
    st = _state(
        broad_hits=[_doc(0, "ERROR", "UnknownHostException: api.partner.com", "api")],
    )
    out = await third_party_probe(st, _config(_ctx([])))
    assert out["third_party"].involved is True

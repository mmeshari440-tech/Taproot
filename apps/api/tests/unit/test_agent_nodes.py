"""Node-body tests for T-21 (normalize → broad search → select → thread_walk).

Nodes are exercised through the ``@node`` decorator (they take a state + a graph
config carrying the :class:`AgentContext`), the same way LangGraph invokes them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from taproot.agent.context import AgentContext
from taproot.agent.nodes import (
    appdynamics_enrich,
    code_locate,
    elastic_broad_search,
    normalize_query,
    occurrence_stats,
    select_threads,
    sentry_enrich,
    synthesize,
    third_party_probe,
    thread_walk,
    verify,
)
from taproot.agent.schemas import (
    AppDFinding,
    InvestigationResult,
    LogThread,
    SentryFinding,
    SeverityScore,
)
from taproot.agent.state import InvestigationState
from taproot.core.models import (
    AppDErrorSnapshot,
    BroadSearchResult,
    DayBucket,
    ExitCall,
    Frame,
    LogDoc,
    RepoRef,
    SentryEventDetail,
    SentryIssue,
)
from taproot.integrations.llm import FakeLLM

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


def _ctx(
    clients: list[Any],
    *,
    max_threads: int = 5,
    sentry: list[Any] | None = None,
    appd: list[Any] | None = None,
    resolver: Any = None,
    llm: Any = None,
    max_tokens: int = 120_000,
) -> AgentContext:
    async def emit_start(_n: str, _t: str) -> int:
        return 1

    async def emit_finish(_seq: int, _n: str, _s: str, _summary: str | None) -> None:
        return None

    return AgentContext(
        emit_start,
        emit_finish,
        max_threads=max_threads,
        elastic_clients=clients,
        sentry_clients=sentry or [],
        appdynamics_clients=appd or [],
        code_resolver=resolver,
        llm=llm,
        max_tokens=max_tokens,
    )


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


# --- sentry_enrich / appdynamics_enrich (T-24) ------------------------------
class _FakeSentry:
    def __init__(self, issues: list[SentryIssue], event: SentryEventDetail | None = None) -> None:
        self._issues = issues
        self._event = event

    async def search_issues(self, query: str, *, limit: int = 10) -> list[SentryIssue]:
        return self._issues

    async def latest_event(self, issue_id: str) -> SentryEventDetail:
        assert self._event is not None
        return self._event


class _FakeAppD:
    def __init__(
        self, snapshots: list[AppDErrorSnapshot], metrics: list[float] | None = None
    ) -> None:
        self._snapshots = snapshots
        self._metrics = metrics or []

    async def error_snapshots(self, *, duration_mins: int = 60) -> list[AppDErrorSnapshot]:
        return self._snapshots

    async def metric_data(self, metric_path: str, *, duration_mins: int = 60) -> list[float]:
        return self._metrics


async def test_sentry_enrich_populates_finding() -> None:
    issue = SentryIssue(id="42", title="NPE", culprit="Pay.charge", user_count=7)
    event = SentryEventDetail(
        event_id="e1",
        release="deadbeef",
        frames=[Frame(filename="pay.py", in_app=True), Frame(filename="lib.py", in_app=False)],
    )
    ctx = _ctx([], sentry=[_FakeSentry([issue], event)])
    out = await sentry_enrich(_state(), _config(ctx))
    finding = out["sentry"]
    assert finding.skipped is False
    assert finding.issue_id == "42"
    assert finding.culprit == "Pay.charge"
    assert finding.release == "deadbeef"
    assert finding.user_count == 7
    assert any(f.in_app for f in finding.frames)


async def test_sentry_enrich_skips_when_unconfigured() -> None:
    out = await sentry_enrich(_state(), _config(_ctx([])))
    assert out["sentry"].skipped is True
    assert "no Sentry integration" in out["sentry"].skip_reason


async def test_sentry_enrich_skips_on_no_match() -> None:
    out = await sentry_enrich(_state(), _config(_ctx([], sentry=[_FakeSentry([])])))
    assert out["sentry"].skipped is True
    assert "no matching" in out["sentry"].skip_reason


async def test_appdynamics_enrich_aggregates_exit_calls() -> None:
    snaps = [
        AppDErrorSnapshot(
            id="s1", exit_calls=[ExitCall(target="db", call_type="JDBC", error_count=2)]
        ),
        AppDErrorSnapshot(
            id="s2",
            exit_calls=[
                ExitCall(target="db", call_type="JDBC", error_count=3),
                ExitCall(target="cache", call_type="HTTP", error_count=1),
            ],
        ),
    ]
    ctx = _ctx([], appd=[_FakeAppD(snaps, metrics=[2.0, 4.0])])
    out = await appdynamics_enrich(_state(), _config(ctx))
    finding = out["appdynamics"]
    assert finding.skipped is False
    assert finding.bt_health == "degraded"
    assert finding.error_rate == 3.0  # mean of [2.0, 4.0]
    top = finding.exit_calls[0]
    assert top.target == "db" and top.error_count == 5  # merged across snapshots, ranked first


async def test_appdynamics_enrich_healthy_when_no_snapshots() -> None:
    out = await appdynamics_enrich(_state(), _config(_ctx([], appd=[_FakeAppD([])])))
    assert out["appdynamics"].bt_health == "healthy"
    assert out["appdynamics"].skipped is False


async def test_appdynamics_enrich_skips_when_unconfigured() -> None:
    out = await appdynamics_enrich(_state(), _config(_ctx([])))
    assert out["appdynamics"].skipped is True
    assert "no AppDynamics integration" in out["appdynamics"].skip_reason


# --- code_locate (T-25) -----------------------------------------------------
_SRC = "\n".join(f"line{i}" for i in range(1, 101))  # 100-line file


class _FakeResolver:
    def __init__(self, repos: list[RepoRef], files: dict[str, str] | None = None) -> None:
        self._repos = repos
        self._files = files or {}
        self.requested: list[tuple[str, str]] = []

    def repos(self) -> list[RepoRef]:
        return self._repos

    async def fetch_file(self, gitlab_project_id: int, path: str, ref: str) -> str | None:
        self.requested.append((path, ref))
        return self._files.get(path)


def _be_repo(**kw: Any) -> RepoRef:
    base: dict[str, Any] = {
        "name": "be-pay",
        "gitlab_project_id": 7,
        "default_branch": "main",
        "org_package_prefixes": ["src/"],
    }
    base.update(kw)
    return RepoRef(**base)


async def _locate(state: InvestigationState, resolver: Any) -> list[Any]:
    out = await code_locate(state, _config(_ctx([], resolver=resolver)))
    return out["code_locations"]


async def test_code_locate_happy_path_python_frame() -> None:
    err = 'Traceback:\n  File "src/svc/payment.py", line 50, in charge\n    do_charge()'
    resolver = _FakeResolver([_be_repo()], {"src/svc/payment.py": _SRC})
    locs = await _locate(_state(error_text=err), resolver)
    assert len(locs) == 1
    loc = locs[0]
    assert loc.repo == "be-pay"
    assert loc.path == "src/svc/payment.py"
    assert loc.line == 50
    assert loc.ref == "main"
    assert "line50" in loc.snippet
    assert "line25" in loc.snippet and "line75" in loc.snippet  # ±25 window
    assert loc.why


async def test_code_locate_file_missing_reports_path_without_snippet() -> None:
    err = '  File "src/svc/payment.py", line 50, in charge'
    resolver = _FakeResolver([_be_repo()], files={})  # nothing resolves
    locs = await _locate(_state(error_text=err), resolver)
    assert len(locs) == 1
    assert locs[0].snippet is None
    assert "not found" in locs[0].why


async def test_code_locate_unregistered_repo_reports_path_no_snippet() -> None:
    sentry = SentryFinding(frames=[Frame(filename="vendor/thing.py", in_app=True, lineno=3)])
    resolver = _FakeResolver([_be_repo()])  # prefix "src/" won't match "vendor/…"
    locs = await _locate(_state(sentry=sentry), resolver)
    assert len(locs) == 1
    assert locs[0].repo == "(unregistered)"
    assert locs[0].snippet is None


async def test_code_locate_skips_minified_fe_frame() -> None:
    sentry = SentryFinding(frames=[Frame(filename="static/main.abcdef12.js", in_app=True)])
    resolver = _FakeResolver([_be_repo()])
    locs = await _locate(_state(sentry=sentry), resolver)
    assert locs == []


async def test_code_locate_handles_monorepo_path_prefix_mismatch() -> None:
    err = '  File "/monorepo/apps/web/src/pay.py", line 12, in run'
    # The repo root only has src/pay.py — the leading monorepo segments must be stripped.
    resolver = _FakeResolver([_be_repo()], {"src/pay.py": _SRC})
    locs = await _locate(_state(error_text=err), resolver)
    assert len(locs) == 1
    assert locs[0].path == "src/pay.py"
    assert locs[0].snippet is not None


async def test_code_locate_uses_sentry_release_sha_as_ref() -> None:
    sentry = SentryFinding(
        frames=[Frame(filename="src/pay.py", in_app=True, lineno=10)],
        release="be-pay@1.0.0+deadbeef",
    )
    resolver = _FakeResolver([_be_repo()], {"src/pay.py": _SRC})
    locs = await _locate(_state(sentry=sentry), resolver)
    assert locs[0].ref == "deadbeef"
    assert locs[0].confidence == 0.85


async def test_code_locate_skips_without_resolver() -> None:
    out = await code_locate(_state(error_text="boom"), _config(_ctx([])))
    assert out["code_locations"] == []


async def test_code_locate_caps_at_five() -> None:
    lines = "\n".join(f'  File "src/mod{i}.py", line {i}, in f' for i in range(1, 9))
    files = {f"src/mod{i}.py": _SRC for i in range(1, 9)}
    resolver = _FakeResolver([_be_repo()], files)
    locs = await _locate(_state(error_text=lines), resolver)
    assert len(locs) == 5


# --- occurrence_stats (T-26) ------------------------------------------------
class _HistElastic:
    def __init__(self, buckets: list[DayBucket], users: int = 0) -> None:
        self._buckets = buckets
        self._users = users
        self.hist_window: int | None = None
        self.card_window: int | None = None

    async def histogram(self, signature: str, *, window_days: int = 7) -> list[DayBucket]:
        self.hist_window = window_days
        return self._buckets

    async def cardinality(
        self, signature: str, *, window_days: int = 7, field: str = "user_name"
    ) -> int:
        self.card_window = window_days
        return self._users


async def test_occurrence_stats_fills_gaps_and_computes_wow() -> None:
    today = datetime.now(UTC).date()

    def iso(days_ago: int) -> str:
        return (today - timedelta(days=days_ago)).isoformat()

    # prior week (days 13,12) totals 4; this week (days 3,1) totals 10.
    buckets = [
        DayBucket(date=iso(13), count=1),
        DayBucket(date=iso(12), count=3),
        DayBucket(date=iso(3), count=6),
        DayBucket(date=iso(1), count=4),
    ]
    fake = _HistElastic(buckets, users=5)
    out = await occurrence_stats(_state(), _config(_ctx([fake])))
    stats = out["stats"]

    assert len(stats.series) == 7  # one bucket per day in the window
    assert any(b.count == 0 for b in stats.series)  # zero-count days present (no gaps)
    assert [b.date for b in stats.series] == [iso(d) for d in range(6, -1, -1)]  # chronological
    assert stats.distinct_users == 5
    assert stats.wow_delta == pytest.approx(1.5)  # (10 - 4) / 4
    assert fake.hist_window == 14  # two weeks queried for WoW
    assert fake.card_window == 7  # distinct users over the window


async def test_occurrence_stats_wow_none_when_no_prior_week() -> None:
    today = datetime.now(UTC).date()
    buckets = [DayBucket(date=(today - timedelta(days=1)).isoformat(), count=3)]
    out = await occurrence_stats(_state(), _config(_ctx([_HistElastic(buckets, users=2)])))
    assert out["stats"].wow_delta is None  # prior week empty → no delta


async def test_occurrence_stats_sums_across_apps() -> None:
    today = datetime.now(UTC).date()
    b = [DayBucket(date=today.isoformat(), count=2)]
    out = await occurrence_stats(
        _state(), _config(_ctx([_HistElastic(b, users=3), _HistElastic(b, users=4)]))
    )
    assert out["stats"].distinct_users == 7  # summed across apps
    assert out["stats"].series[-1].count == 4  # today's counts merged (2 + 2)


async def test_occurrence_stats_skips_without_clients() -> None:
    out = await occurrence_stats(_state(), _config(_ctx([])))
    assert out["stats"].series == []
    assert out["stats"].distinct_users == 0


# --- synthesize / verify (T-27) ----------------------------------------------
def _result_with_citations(*refs: str) -> InvestigationResult:
    return InvestigationResult(
        severity="MEDIUM",
        confidence=0.8,
        root_cause="cause text",
        root_cause_evidence=[
            {"source": "elastic", "ref": r, "excerpt": f"excerpt for {r}"} for r in refs
        ],
    )


_VALID_SYNTH_JSON = json.dumps(
    {
        "severity": "LOW",
        "severity_rationale": "matches baseline",
        "confidence": 0.8,
        "root_cause": "cause",
        "root_cause_evidence": [],
        "open_questions": [],
    }
)


async def test_synthesize_zero_evidence_returns_insufficient_data_without_llm_call() -> None:
    fake = FakeLLM()
    out = await synthesize(_state(), _config(_ctx([], llm=fake)))
    result = out["result"]
    assert result.root_cause is None
    assert result.confidence == 0.0
    assert any("insufficient" in q.lower() for q in result.open_questions)
    assert fake.received == []  # never called the LLM — no fabrication risk


async def test_synthesize_happy_path_validates_and_persists_citations() -> None:
    thread = LogThread(
        txn_id="txn-1", summary="NPE while charging; partner-api returned empty body"
    )
    sentry = SentryFinding(issue_id="42", culprit="Pay.charge", release="deadbeef", user_count=3)
    score = SeverityScore(score=5, severity="MEDIUM", breakdown={"users": 2})
    state = _state(threads=[thread], sentry=sentry, severity_score=score)

    response = json.dumps(
        {
            "severity": "MEDIUM",
            "severity_rationale": "matches deterministic score",
            "confidence": 0.9,
            "root_cause": "The partner API returned an empty body, causing a NullPointerException.",
            "root_cause_evidence": [
                {"source": "elastic", "ref": "E1", "excerpt": "NPE while charging"},
                {"source": "sentry", "ref": "E2", "excerpt": "culprit=Pay.charge"},
            ],
            "open_questions": [],
        }
    )
    fake = FakeLLM([response])
    out = await synthesize(state, _config(_ctx([], llm=fake)))

    result = out["result"]
    assert result.severity == "MEDIUM"
    assert result.root_cause.startswith("The partner API")
    assert [c["ref"] for c in result.root_cause_evidence] == ["E1", "E2"]
    # penalty: appdynamics missing (0.1) + no code_locations (0.05) + 1 thread (0.05) = 0.2
    assert result.confidence == pytest.approx(0.7)
    assert out["tokens_used"] > 0
    assert len(fake.received) == 1


async def test_synthesize_truncates_evidence_oldest_first_within_token_budget() -> None:
    old = LogThread(
        txn_id="txn-old",
        summary="x" * 4000,  # ~1000 tokens
        docs=[_doc(0, "ERROR", "old", "api")],
    )
    new = LogThread(
        txn_id="txn-new",
        summary="y" * 4000,
        docs=[_doc(0, "ERROR", "new", "api", txn="txn-new")],
    )
    old.docs[0].timestamp = datetime(2020, 1, 1, tzinfo=UTC)
    new.docs[0].timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    state = _state(threads=[old, new])

    # _EVIDENCE_RESERVE_TOKENS=4000 headroom -> budget=1000, exactly one ~1000-token item.
    ctx = _ctx([], llm=FakeLLM([_VALID_SYNTH_JSON]), max_tokens=5000)
    out = await synthesize(state, _config(ctx))

    assert out["result"].root_cause == "cause"
    assert any("truncated" in q for q in out["result"].open_questions)


async def test_synthesize_confidence_penalized_for_skipped_integrations() -> None:
    thread = LogThread(txn_id="txn-1", summary="boom")
    sentry = SentryFinding(skipped=True, skip_reason="no Sentry integration configured")
    appd = AppDFinding(skipped=True, skip_reason="no AppDynamics integration configured")
    state = _state(threads=[thread], sentry=sentry, appdynamics=appd)

    fake = FakeLLM([_VALID_SYNTH_JSON.replace('"confidence": 0.8', '"confidence": 0.9')])
    out = await synthesize(state, _config(_ctx([], llm=fake)))
    result = out["result"]
    # penalty: sentry (0.1) + appd (0.1) skipped, no code_locations (0.05), 1 thread (0.05) = 0.3
    assert result.confidence == pytest.approx(0.6)
    assert any("Sentry" in q for q in result.open_questions)
    assert any("AppDynamics" in q for q in result.open_questions)


async def test_synthesize_clamps_severity_to_one_level_above_baseline() -> None:
    thread = LogThread(txn_id="txn-1", summary="boom")
    score = SeverityScore(score=1, severity="LOW", breakdown={})
    state = _state(threads=[thread], severity_score=score)

    response = json.dumps(
        {
            "severity": "BLOCKER",
            "severity_rationale": "escalating",
            "confidence": 0.5,
            "root_cause": "root cause text",
            "root_cause_evidence": [],
            "open_questions": [],
        }
    )
    fake = FakeLLM([response])
    out = await synthesize(state, _config(_ctx([], llm=fake)))
    assert out["result"].severity == "MEDIUM"  # clamped from BLOCKER to LOW + 1


async def test_synthesize_repairs_invalid_json_then_succeeds() -> None:
    thread = LogThread(txn_id="txn-1", summary="boom")
    state = _state(threads=[thread])
    fake = FakeLLM(["not json at all", _VALID_SYNTH_JSON])
    out = await synthesize(state, _config(_ctx([], llm=fake)))
    assert out["result"].root_cause == "cause"
    assert len(fake.received) == 2
    assert len(fake.received[1]) == 4  # system, user, assistant(bad), user(repair)


async def test_synthesize_fails_after_exhausting_repair_attempts() -> None:
    thread = LogThread(txn_id="txn-1", summary="boom")
    state = _state(threads=[thread])
    fake = FakeLLM(["bad1", "bad2", "bad3"])
    with pytest.raises(RuntimeError) as exc_info:
        await synthesize(state, _config(_ctx([], llm=fake)))
    assert "bad3" in str(exc_info.value)  # raw output stored, never silently dropped
    assert len(fake.received) == 3  # initial + 2 repairs, then fail


async def test_verify_skips_when_no_citations() -> None:
    state = _state(result=InvestigationResult(severity="LOW", confidence=0.0, root_cause=None))
    fake = FakeLLM()
    out = await verify(state, _config(_ctx([], llm=fake)))
    assert out == {"verify_retry_needed": False}
    assert fake.received == []


async def test_verify_drops_unsupported_and_lowers_confidence() -> None:
    thread = LogThread(txn_id="txn-1", summary="boom")
    sentry = SentryFinding(issue_id="42", culprit="Pay.charge")
    state = _state(threads=[thread], sentry=sentry, result=_result_with_citations("E1", "E2"))

    response = json.dumps(
        {
            "citations": [
                {"ref": "E1", "supported": True, "reason": "matches"},
                {"ref": "E2", "supported": False, "reason": "not substantiated"},
            ],
            "notes": ["Sentry evidence was inconclusive."],
        }
    )
    fake = FakeLLM([response])
    out = await verify(state, _config(_ctx([], llm=fake)))

    updated = out["result"]
    assert [c["ref"] for c in updated.root_cause_evidence] == ["E1"]
    assert updated.confidence == pytest.approx(0.7)  # 0.8 - 0.1 * 1 dropped
    assert "Sentry evidence was inconclusive." in updated.open_questions
    assert any("dropped" in q for q in updated.open_questions)
    assert out["verify_retry_needed"] is False


async def test_verify_retries_synthesis_when_most_claims_unsupported() -> None:
    thread = LogThread(txn_id="txn-1", summary="boom")
    state = _state(threads=[thread], result=_result_with_citations("E1"), verify_retries=0)

    response = json.dumps({"citations": [{"ref": "E1", "supported": False}], "notes": []})
    fake = FakeLLM([response])
    out = await verify(state, _config(_ctx([], llm=fake)))

    assert out["verify_retry_needed"] is True
    assert out["verify_retries"] == 1
    assert "result" not in out  # leave the prior result for synthesize to overwrite


async def test_verify_finalizes_after_retry_budget_exhausted() -> None:
    thread = LogThread(txn_id="txn-1", summary="boom")
    state = _state(threads=[thread], result=_result_with_citations("E1"), verify_retries=1)

    response = json.dumps({"citations": [{"ref": "E1", "supported": False}], "notes": []})
    fake = FakeLLM([response])
    out = await verify(state, _config(_ctx([], llm=fake)))

    assert out["verify_retry_needed"] is False
    assert out["result"].root_cause_evidence == []


async def test_verify_falls_back_to_syntactic_check_when_llm_fails() -> None:
    thread = LogThread(txn_id="txn-1", summary="boom")
    state = _state(threads=[thread], result=_result_with_citations("E1", "E-missing"))
    fake = FakeLLM(["not json", "still not json", "nope"])
    out = await verify(state, _config(_ctx([], llm=fake)))

    updated = out["result"]
    assert [c["ref"] for c in updated.root_cause_evidence] == ["E1"]  # E-missing isn't a real id

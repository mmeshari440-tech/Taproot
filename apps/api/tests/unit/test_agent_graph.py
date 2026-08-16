from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from taproot.agent.context import AgentContext
from taproot.agent.graph import run_graph
from taproot.agent.nodes import ALL_NODES, PARALLEL_NODES, node
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

# Scripted so `synthesize` -> `verify` both get valid JSON on the first try;
# "E1" is always the id of the first thread's evidence (thread_walk runs before
# any other evidence-producing node).
_SYNTH_RESPONSE = json.dumps(
    {
        "severity": "LOW",
        "severity_rationale": "matches the deterministic baseline",
        "confidence": 0.8,
        "root_cause": "NullPointerException while processing the transaction.",
        "root_cause_evidence": [{"source": "elastic", "ref": "E1", "excerpt": "boom"}],
        "open_questions": [],
    }
)
_VERIFY_RESPONSE = json.dumps(
    {"citations": [{"ref": "E1", "supported": True, "reason": "matches"}], "notes": []}
)


def _state() -> InvestigationState:
    return InvestigationState(
        investigation_id=uuid4(), project_id=uuid4(), error_text="NullPointerException"
    )


class _FakeElastic:
    """Minimal ElasticSearcher: one candidate transaction with a small thread."""

    async def search(
        self, error_text: str, *, window_days: int = 7, size: int = 50
    ) -> BroadSearchResult:
        return BroadSearchResult(
            docs=[LogDoc(severity="ERROR", message="boom", transaction_id="txn-1")],
            transaction_ids=["txn-1"],
        )

    async def thread(self, transaction_id: str, *, size: int = 500) -> list[LogDoc]:
        return [LogDoc(severity="ERROR", message="boom", transaction_id=transaction_id)]

    async def histogram(self, signature: str, *, window_days: int = 7) -> list[DayBucket]:
        return [DayBucket(date="2026-08-01", count=1)]

    async def cardinality(
        self, signature: str, *, window_days: int = 7, field: str = "user_name"
    ) -> int:
        return 1


class _FakeSentry:
    async def search_issues(self, query: str, *, limit: int = 10) -> list[SentryIssue]:
        return [SentryIssue(id="1", title="NPE", culprit="Pay.charge", user_count=3)]

    async def latest_event(self, issue_id: str) -> SentryEventDetail:
        return SentryEventDetail(
            event_id="e1", release="abc123", frames=[Frame(filename="pay.py", in_app=True)]
        )


class _FakeAppD:
    async def error_snapshots(self, *, duration_mins: int = 60) -> list[AppDErrorSnapshot]:
        return [
            AppDErrorSnapshot(
                id="s1",
                error_message="boom",
                exit_calls=[ExitCall(target="db", call_type="JDBC", error_count=2)],
            )
        ]

    async def metric_data(self, metric_path: str, *, duration_mins: int = 60) -> list[float]:
        return [1.0, 2.0]


class _FakeResolver:
    def repos(self) -> list[RepoRef]:
        return [RepoRef(name="be", gitlab_project_id=1, org_package_prefixes=["pay"])]

    async def fetch_file(self, gitlab_project_id: int, path: str, ref: str) -> str | None:
        return "\n".join(f"line{i}" for i in range(1, 60))


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self._seq = 0

    def context(
        self,
        *,
        node_timeout_s: float = 45.0,
        max_duration_s: float = 300.0,
        with_clients: bool = False,
    ) -> AgentContext:
        async def emit_start(node_name: str, _title: str) -> int:
            self._seq += 1
            self.events.append(("start", node_name))
            return self._seq

        async def emit_finish(seq: int, node_name: str, status: str, summary: str | None) -> None:
            self.events.append(("finish", node_name, status))

        return AgentContext(
            emit_start,
            emit_finish,
            node_timeout_s,
            max_duration_s,
            elastic_clients=[_FakeElastic()] if with_clients else [],
            sentry_clients=[_FakeSentry()] if with_clients else [],
            appdynamics_clients=[_FakeAppD()] if with_clients else [],
            code_resolver=_FakeResolver() if with_clients else None,
            # Evidence only exists when `with_clients=True`; the no-evidence path
            # short-circuits synthesize without ever calling the LLM.
            llm=FakeLLM([_SYNTH_RESPONSE, _VERIFY_RESPONSE]) if with_clients else None,
        )

    def started(self) -> set[str]:
        return {e[1] for e in self.events if e[0] == "start"}

    def finished_ok(self) -> set[str]:
        return {e[1] for e in self.events if e[0] == "finish" and e[2] == "ok"}


# --- full graph -------------------------------------------------------------
async def test_graph_runs_all_nodes_and_produces_result() -> None:
    rec = _Recorder()
    final = await run_graph(_state(), rec.context(with_clients=True))

    # Every node emitted start + a successful finish.
    expected = {fn.__name__ for fn in ALL_NODES}
    assert rec.started() == expected
    assert rec.finished_ok() == expected

    assert final["result"] is not None
    assert final["result"].severity == "LOW"  # astream keeps nested models as instances
    assert final["node_errors"] == {}


async def test_no_hits_skips_deep_dive_and_synthesizes() -> None:
    # No Elastic client → zero candidates → route straight to synthesize (clean abort).
    rec = _Recorder()
    final = await run_graph(_state(), rec.context())

    started = rec.started()
    assert started == {"normalize_query", "elastic_broad_search", "synthesize", "verify"}
    assert "select_threads" not in started
    assert not set(PARALLEL_NODES) & started
    assert final["result"] is not None
    assert final["node_errors"] == {}


async def test_parallel_nodes_write_disjoint_fields() -> None:
    rec = _Recorder()
    final = await run_graph(_state(), rec.context(with_clients=True))
    # All five fan-out fields populated → LangGraph merged disjoint updates cleanly.
    assert final["third_party"] is not None
    assert final["sentry"] is not None
    assert final["appdynamics"] is not None
    assert final["stats"] is not None
    assert "code_locations" in final
    assert set(PARALLEL_NODES) <= rec.finished_ok()


async def test_max_duration_returns_partial() -> None:
    rec = _Recorder()
    final = await run_graph(_state(), rec.context(max_duration_s=0.0))
    assert "_deadline" in final["node_errors"]


# --- node contract (decorator) ---------------------------------------------
def _config(ctx: AgentContext) -> dict:
    return {"configurable": {"ctx": ctx}}


async def test_failing_noncritical_node_is_isolated() -> None:
    rec = _Recorder()

    @node("boom", "Boom")
    async def boom(_state: InvestigationState, _ctx: object) -> dict:
        raise ValueError("kaboom")

    result = await boom(_state(), _config(rec.context()))
    assert result == {"node_errors": {"boom": "kaboom"}}
    assert ("finish", "boom", "failed") in rec.events


async def test_failing_critical_node_raises() -> None:
    rec = _Recorder()

    @node("crit", "Critical", critical=True)
    async def crit(_state: InvestigationState, _ctx: object) -> dict:
        raise ValueError("fatal")

    with pytest.raises(ValueError, match="fatal"):
        await crit(_state(), _config(rec.context()))


async def test_node_can_finish_skipped_via_sentinel() -> None:
    rec = _Recorder()

    @node("skip", "Skip")
    async def skip(_state: InvestigationState, _ctx: object) -> dict:
        return {"_status": "skipped", "_summary": "nothing to do"}

    result = await skip(_state(), _config(rec.context()))
    assert result == {}  # sentinels are consumed by the decorator
    assert ("finish", "skip", "skipped") in rec.events


async def test_node_timeout_is_isolated() -> None:
    rec = _Recorder()

    @node("slow", "Slow")
    async def slow(_state: InvestigationState, _ctx: object) -> dict:
        await asyncio.sleep(1)
        return {}

    result = await slow(_state(), _config(rec.context(node_timeout_s=0.01)))
    assert "slow" in result["node_errors"]

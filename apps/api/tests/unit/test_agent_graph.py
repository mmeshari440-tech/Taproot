from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from taproot.agent.context import AgentContext
from taproot.agent.graph import run_graph
from taproot.agent.nodes import ALL_NODES, PARALLEL_NODES, node
from taproot.agent.state import InvestigationState
from taproot.core.models import BroadSearchResult, LogDoc


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


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self._seq = 0

    def context(
        self,
        *,
        node_timeout_s: float = 45.0,
        max_duration_s: float = 300.0,
        with_elastic: bool = False,
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
            elastic_clients=[_FakeElastic()] if with_elastic else [],
        )

    def started(self) -> set[str]:
        return {e[1] for e in self.events if e[0] == "start"}

    def finished_ok(self) -> set[str]:
        return {e[1] for e in self.events if e[0] == "finish" and e[2] == "ok"}


# --- full graph -------------------------------------------------------------
async def test_graph_runs_all_nodes_and_produces_result() -> None:
    rec = _Recorder()
    final = await run_graph(_state(), rec.context(with_elastic=True))

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
    final = await run_graph(_state(), rec.context(with_elastic=True))
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


async def test_node_timeout_is_isolated() -> None:
    rec = _Recorder()

    @node("slow", "Slow")
    async def slow(_state: InvestigationState, _ctx: object) -> dict:
        await asyncio.sleep(1)
        return {}

    result = await slow(_state(), _config(rec.context(node_timeout_s=0.01)))
    assert "slow" in result["node_errors"]

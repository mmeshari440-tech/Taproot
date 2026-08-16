"""LangGraph assembly + runner (ARCHITECTURE.md §6.1, T-20).

Nodes 1→4 run in sequence; 5–9 fan out in parallel and fan in at
``severity_score``; then ``synthesize`` → ``verify`` (with at most one retry back
to ``synthesize``). ``run_graph`` streams state and enforces the max-duration
budget, returning partial results if the deadline is hit.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph

from taproot.agent import nodes
from taproot.agent.context import CONFIG_KEY, AgentContext
from taproot.agent.state import InvestigationState


def _route_after_verify(state: InvestigationState) -> str:
    # `verify` (T-27) only sets this when too many claims were unsupported and
    # the retry budget (nodes.MAX_VERIFY_RETRIES) isn't exhausted yet.
    return "retry" if state.verify_retry_needed else "end"


def _route_after_broad(state: InvestigationState) -> str:
    """Zero candidate transactions → skip the deep dive and synthesize directly
    (a clean 'insufficient evidence' abort per T-21)."""
    return "continue" if state.candidate_txn_ids else "empty"


def build_graph() -> Any:
    # Typed as Any: LangGraph's builder generics add friction without safety here.
    graph: Any = StateGraph(InvestigationState)
    for fn in nodes.ALL_NODES:
        graph.add_node(fn.__name__, fn)

    graph.add_edge(START, "normalize_query")
    graph.add_edge("normalize_query", "elastic_broad_search")
    graph.add_conditional_edges(
        "elastic_broad_search",
        _route_after_broad,
        {"continue": "select_threads", "empty": "synthesize"},
    )
    graph.add_edge("select_threads", "thread_walk")

    # Fan out to the parallel enrichment nodes, fan in at severity_score.
    for name in nodes.PARALLEL_NODES:
        graph.add_edge("thread_walk", name)
        graph.add_edge(name, "severity_score")

    graph.add_edge("severity_score", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_conditional_edges("verify", _route_after_verify, {"retry": "synthesize", "end": END})
    return graph.compile()


async def run_graph(state: InvestigationState, ctx: AgentContext) -> dict[str, Any]:
    """Run the graph, enforcing the max-duration budget. Returns the latest
    (possibly partial) state as a dict."""
    app = build_graph()
    config = {"configurable": {CONFIG_KEY: ctx}, "recursion_limit": 50}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ctx.max_duration_s

    final: dict[str, Any] = state.model_dump()
    async for snapshot in app.astream(state, config=config, stream_mode="values"):
        final = snapshot
        if loop.time() >= deadline:
            final.setdefault("node_errors", {})["_deadline"] = "max duration exceeded"
            break
    return final

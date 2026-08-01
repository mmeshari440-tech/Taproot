"""Graph nodes (ARCHITECTURE.md §6.1, §6.3).

Sprint 3 T-20 wires all nodes as **stubs** behind the real node contract:
every node emits ``step.start`` before work and exactly one ``step.finish``; the
body runs under a per-node timeout; a failure is isolated to ``node_errors`` and
returns normally — except the two critical nodes (``elastic_broad_search``,
``synthesize``) whose failure aborts the run. Node bodies are filled in
T-21–T-28. (They live here as one module for the skeleton; they split into
``nodes/<name>.py`` as they are implemented.)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig

from taproot.agent.context import ctx_from_config
from taproot.agent.schemas import (
    AppDFinding,
    InvestigationResult,
    OccurrenceStats,
    QuerySignals,
    SentryFinding,
    SeverityScore,
    ThirdPartyFinding,
)
from taproot.agent.state import InvestigationState

NodeBody = Callable[[InvestigationState, Any], Awaitable[dict[str, Any]]]
GraphNode = Callable[[InvestigationState, RunnableConfig], Awaitable[dict[str, Any]]]

# The only nodes whose failure aborts the whole investigation (ARCHITECTURE.md §6.3).
CRITICAL = {"elastic_broad_search", "synthesize"}


def node(name: str, title: str, *, critical: bool = False) -> Callable[[NodeBody], GraphNode]:
    """Wrap a node body with the node contract (step events, timeout, isolation)."""

    def decorate(body: NodeBody) -> GraphNode:
        async def wrapped(state: InvestigationState, config: RunnableConfig) -> dict[str, Any]:
            ctx = ctx_from_config(config)
            seq = await ctx.emit_start(name, title)
            try:
                update = await asyncio.wait_for(body(state, ctx), timeout=ctx.node_timeout_s)
            except Exception as exc:
                await ctx.emit_finish(seq, name, "failed", str(exc))
                if critical:
                    raise
                return {"node_errors": {name: str(exc)}}
            summary = str(update.pop("_summary", f"{name} complete"))
            await ctx.emit_finish(seq, name, "ok", summary)
            return update

        wrapped.__name__ = name
        return wrapped

    return decorate


# --- nodes 1–4 --------------------------------------------------------------
@node("normalize_query", "Normalizing the error")
async def normalize_query(state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"signals": QuerySignals(time_window_days=state.time_window_days)}


@node("elastic_broad_search", "Searching Elasticsearch", critical=True)
async def elastic_broad_search(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"broad_hits": [], "candidate_txn_ids": []}


@node("select_threads", "Selecting transactions to walk")
async def select_threads(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {}


@node("thread_walk", "Walking transaction threads")
async def thread_walk(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"threads": []}


# --- nodes 5–9 (parallel — disjoint fields) ---------------------------------
@node("third_party_probe", "Probing for third-party failures")
async def third_party_probe(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"third_party": ThirdPartyFinding()}


@node("sentry_enrich", "Enriching from Sentry")
async def sentry_enrich(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"sentry": SentryFinding(skipped=True, skip_reason="stub")}


@node("appdynamics_enrich", "Enriching from AppDynamics")
async def appdynamics_enrich(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"appdynamics": AppDFinding(skipped=True, skip_reason="stub")}


@node("code_locate", "Locating code")
async def code_locate(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"code_locations": []}


@node("occurrence_stats", "Computing occurrence statistics")
async def occurrence_stats(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"stats": OccurrenceStats()}


# --- scoring + synthesis ----------------------------------------------------
@node("severity_score", "Scoring severity")
async def severity_score(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"severity_score": SeverityScore(severity="LOW")}


@node("synthesize", "Synthesizing the result", critical=True)
async def synthesize(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {
        "result": InvestigationResult(
            severity="LOW",
            confidence=0.0,
            root_cause="(skeleton) agent nodes are stubs — implemented in T-21+.",
            open_questions=["Agent node bodies are not yet implemented."],
        )
    }


@node("verify", "Verifying claims")
async def verify(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {}


ALL_NODES: list[GraphNode] = [
    normalize_query,
    elastic_broad_search,
    select_threads,
    thread_walk,
    third_party_probe,
    sentry_enrich,
    appdynamics_enrich,
    code_locate,
    occurrence_stats,
    severity_score,
    synthesize,
    verify,
]

PARALLEL_NODES = [
    "third_party_probe",
    "sentry_enrich",
    "appdynamics_enrich",
    "code_locate",
    "occurrence_stats",
]

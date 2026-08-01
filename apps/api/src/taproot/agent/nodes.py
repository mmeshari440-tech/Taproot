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
import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig

from taproot.agent.context import ElasticSearcher, ctx_from_config
from taproot.agent.schemas import (
    AppDFinding,
    InvestigationResult,
    LogThread,
    OccurrenceStats,
    QuerySignals,
    SentryFinding,
    SeverityScore,
    ThirdPartyFinding,
)
from taproot.agent.state import InvestigationState
from taproot.core.models import LogDoc

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


# --- helpers (nodes 1–4) ----------------------------------------------------
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ERROR_SEVERITIES = {"ERROR", "FATAL", "CRITICAL"}
_THREAD_SUMMARY_MAX_TOKENS = 400

# An exception/error/fault class name (optionally dotted): ``java.net.SocketTimeoutException``.
_EXCEPTION_RE = re.compile(r"\b([A-Za-z_][\w.]*(?:Error|Exception|Fault|Warning))\b")
# Significant identifier-ish tokens (drop very short noise words below).
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{2,}")
# An explicit service/app hint in the error text.
_SERVICE_HINT_RE = re.compile(
    r"(?:service|svc|container|app|application)[\s=:\"']+([A-Za-z][\w.-]+)", re.IGNORECASE
)
_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "was", "are", "has",
    "not", "but", "error", "exception", "caused", "while", "when", "null",
}


def _aware(ts: datetime | None) -> datetime:
    """Normalize a (possibly naive/absent) timestamp to an aware datetime for
    safe cross-doc comparison."""
    if ts is None:
        return _EPOCH
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _extract_tokens(text: str, *, limit: int = 12) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.findall(text):
        low = match.lower()
        if low in _STOPWORDS or low in seen:
            continue
        seen.add(low)
        tokens.append(match)
        if len(tokens) >= limit:
            break
    return tokens


def _rank_transactions(txn_ids: list[str], docs: list[LogDoc]) -> list[str]:
    """Rank candidate transactions by distinct users, then completeness (doc
    count), then recency — the signals that mark a thread worth walking."""
    by_txn: dict[str, list[LogDoc]] = {}
    for doc in docs:
        if doc.transaction_id:
            by_txn.setdefault(doc.transaction_id, []).append(doc)

    def key(txn: str) -> tuple[int, int, datetime]:
        group = by_txn.get(txn, [])
        users = {d.user_name for d in group if d.user_name}
        recency = max((_aware(d.timestamp) for d in group), default=_EPOCH)
        return (len(users), len(group), recency)

    return sorted(txn_ids, key=key, reverse=True)


async def _fetch_thread(clients: Sequence[ElasticSearcher], txn_id: str) -> list[LogDoc]:
    """First client that returns docs for this transaction wins (ADR-0002: the
    backend app owns the ``transaction_id``)."""
    for client in clients:
        try:
            docs = await client.thread(txn_id, size=500)
        except Exception:  # noqa: S112 - a dead app must not sink the walk; try the next
            continue
        if docs:
            return docs
    return []


def _hash_user(name: str | None) -> str | None:
    """Stable pseudonym so distinct users can be counted without exposing PII.
    (T-22 centralizes redaction; this keeps raw usernames out of state now.)"""
    if not name:
        return None
    return "user_" + hashlib.sha256(name.encode()).hexdigest()[:8]


def _truncate_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4  # ~4 chars/token heuristic (no tokenizer in the agent layer)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " …"


def _oneline(docs: list[LogDoc], *, take: int = 3) -> str:
    msgs = [(d.message or "").strip().replace("\n", " ") for d in docs if d.message]
    if len(msgs) > take:
        msgs = [*msgs[: take - 1], "…", msgs[-1]]
    return " | ".join(m[:160] for m in msgs) or "(no messages)"


def _summarize_thread(
    txn_id: str, docs: list[LogDoc], error_index: int | None, services: list[str]
) -> str:
    where = ", ".join(services) if services else "unknown service(s)"
    lines = [f"Transaction {txn_id} across {where}: {len(docs)} log lines."]
    if error_index is not None:
        preamble, err, aftermath = docs[:error_index], docs[error_index], docs[error_index + 1 :]
        lines.append(f"Preamble ({len(preamble)} lines): {_oneline(preamble)}")
        lines.append(f"Error [{err.severity}]: {(err.message or '').strip()[:200]}")
        lines.append(f"Aftermath ({len(aftermath)} lines): {_oneline(aftermath)}")
    else:
        lines.append(f"No ERROR/FATAL/CRITICAL line found: {_oneline(docs)}")
    return _truncate_tokens("\n".join(lines), _THREAD_SUMMARY_MAX_TOKENS)


def _build_thread(txn_id: str, docs: list[LogDoc]) -> LogThread:
    ordered = sorted(docs, key=lambda d: _aware(d.timestamp))
    error_index = next(
        (i for i, d in enumerate(ordered) if (d.severity or "").upper() in _ERROR_SEVERITIES),
        None,
    )
    services: list[str] = []
    seen: set[str] = set()
    for doc in ordered:
        if doc.service and doc.service not in seen:
            seen.add(doc.service)
            services.append(doc.service)
    user_hash = _hash_user(next((d.user_name for d in ordered if d.user_name), None))
    return LogThread(
        txn_id=txn_id,
        docs=ordered,
        error_index=error_index,
        services=services,
        user_hash=user_hash,
        summary=_summarize_thread(txn_id, ordered, error_index, services),
    )


# --- nodes 1–4 --------------------------------------------------------------
@node("normalize_query", "Normalizing the error")
async def normalize_query(state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    """Deterministically distill the raw error text into search signals: the
    exception class, key tokens, an optional service hint, and the time window."""
    text = state.error_text
    classes = _EXCEPTION_RE.findall(text)
    exception_class = classes[0] if classes else None
    tokens = _extract_tokens(text)
    hint_match = _SERVICE_HINT_RE.search(text)
    service_hint = hint_match.group(1) if hint_match else None

    variants: list[str] = []
    for variant in (exception_class, text.strip(), " ".join(tokens[:6])):
        if variant and variant not in variants:
            variants.append(variant)

    signals = QuerySignals(
        exception_class=exception_class,
        tokens=tokens,
        service_hint=service_hint,
        time_window_days=state.time_window_days,
        search_variants=variants,
    )
    return {
        "signals": signals,
        "_summary": f"{exception_class or 'error'} · {len(tokens)} tokens",
    }


@node("elastic_broad_search", "Searching Elasticsearch", critical=True)
async def elastic_broad_search(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Broad search across every configured app's index (PLAN.md §6.3), merging
    hits and de-duplicating candidate transactions. Aborts only if *all* apps
    error; zero hits routes cleanly to synthesis (see graph._route_after_broad)."""
    clients: Sequence[ElasticSearcher] = ctx.elastic_clients
    if not clients:
        return {
            "broad_hits": [],
            "candidate_txn_ids": [],
            "_summary": "no Elastic app configured for this project",
        }

    window = state.signals.time_window_days if state.signals else state.time_window_days
    docs: list[LogDoc] = []
    txn_ids: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for client in clients:
        try:
            result = await client.search(state.error_text, window_days=window, size=50)
        except Exception as exc:
            errors.append(str(exc))
            continue
        docs.extend(result.docs)
        for txn in result.transaction_ids:
            if txn and txn not in seen:
                seen.add(txn)
                txn_ids.append(txn)

    if errors and len(errors) == len(clients):
        raise RuntimeError("all Elasticsearch apps failed: " + "; ".join(errors))

    return {
        "broad_hits": docs,
        "candidate_txn_ids": txn_ids,
        "_summary": f"{len(docs)} hits across {len(clients)} app(s); {len(txn_ids)} transactions",
    }


@node("select_threads", "Selecting transactions to walk")
async def select_threads(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Rank the candidate transactions and keep the top ``ctx.max_threads`` to
    walk in depth."""
    ranked = _rank_transactions(state.candidate_txn_ids, state.broad_hits)
    selected = ranked[: ctx.max_threads]
    return {
        "candidate_txn_ids": selected,
        "_summary": f"selected {len(selected)} of {len(state.candidate_txn_ids)} transactions",
    }


@node("thread_walk", "Walking transaction threads")
async def thread_walk(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Reconstruct each selected transaction: fetch all severities (@timestamp
    ASC), split preamble/error/aftermath, list the services it touched, and
    summarize it to ≤400 tokens for synthesis."""
    threads: list[LogThread] = []
    for txn_id in state.candidate_txn_ids:
        docs = await _fetch_thread(ctx.elastic_clients, txn_id)
        if docs:
            threads.append(_build_thread(txn_id, docs))
    return {"threads": threads, "_summary": f"walked {len(threads)} thread(s)"}


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

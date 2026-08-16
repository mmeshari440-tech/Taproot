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
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError

from taproot.agent.context import ElasticSearcher, ctx_from_config
from taproot.agent.prompts import render
from taproot.agent.schemas import (
    AppDFinding,
    CodeLocation,
    EvidenceItem,
    InvestigationResult,
    LogThread,
    OccurrenceStats,
    QuerySignals,
    SentryFinding,
    SeverityLevel,
    SeverityScore,
    SynthesizeOutput,
    ThirdPartyFinding,
    VerifyCitation,
    VerifyOutput,
)
from taproot.agent.state import InvestigationState
from taproot.core.models import DayBucket, ExitCall, Frame, LLMMessage, LogDoc, RepoRef
from taproot.core.redaction import hash_user

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
            # Nodes may finish "skipped" (e.g. an unconfigured integration) via a
            # sentinel; default is "ok" (ARCHITECTURE.md §6.3).
            status = str(update.pop("_status", "ok"))
            await ctx.emit_finish(seq, name, status, summary)
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
    user_hash = hash_user(next((d.user_name for d in ordered if d.user_name), None))
    return LogThread(
        txn_id=txn_id,
        docs=ordered,
        error_index=error_index,
        services=services,
        user_hash=user_hash,
        summary=_summarize_thread(txn_id, ordered, error_index, services),
    )


# --- helpers (node 5: third-party probe) ------------------------------------
# Outbound-call failure exception signatures (PLAN.md req; ARCHITECTURE.md §6).
_THIRD_PARTY_EXC_RE = re.compile(
    r"\b(SocketTimeout(?:Exception)?|ConnectTimeout(?:Exception)?|ReadTimeout(?:Exception)?|"
    r"ConnectException|ConnectionRefused(?:Error)?|ConnectionReset(?:Error)?|ConnectionError|"
    r"TimeoutException|UnknownHostException|NoRouteToHostException|SSLHandshakeException|"
    r"GatewayTimeout(?:Exception)?|BadGateway(?:Exception)?|ServiceUnavailable(?:Exception)?)\b"
)
# Downstream HTTP degradation phrases (partner 429 / gateway 5xx).
_GATEWAY_PHRASE_RE = re.compile(
    r"\b(gateway timeout|bad gateway|service unavailable|too many requests)\b", re.IGNORECASE
)
# An HTTP status only counts as a signal when explicitly labelled as one.
_HTTP_STATUS_RE = re.compile(
    r"(?:HTTP[\s/]*\d?\.?\d?\s*|status(?:\s*code)?[\s:=]+)(429|50[234])\b", re.IGNORECASE
)
_URL_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")
# The downstream recovered — a fallback/circuit-breaker/cache absorbed the failure.
_FALLBACK_RE = re.compile(
    r"\b(fell back|fall(?:ing|s)?\s*back|fallback|circuit[\s-]?breaker|"
    r"serv(?:ing|ed)\s+(?:from\s+)?cache|using\s+cache|cached\s+response|"
    r"using\s+default|default\s+value|degraded\s+gracefully|gracefully\s+degraded|"
    r"retry\s+succeeded|recovered)\b",
    re.IGNORECASE,
)
# HTTP statuses that, on an outbound call, indicate downstream (not our) failure.
_PARTNER_STATUS = {429, 502, 503, 504}


def _doc_text(doc: LogDoc) -> str:
    return " ".join(part for part in (doc.message, doc.stack_trace) if part)


def _match_third_party(doc: LogDoc) -> tuple[str, str, str | None] | None:
    """Return ``(symptom, evidence, service)`` if this doc shows an outbound-call
    failure, else ``None``. ``service`` prefers an external hostname in the text."""
    text = _doc_text(doc)
    host = (m.group(1) if (m := _URL_HOST_RE.search(text)) else None) or doc.service

    if exc := _THIRD_PARTY_EXC_RE.search(text):
        return (f"outbound call failure ({exc.group(1)})", text, host)
    if doc.http_status in _PARTNER_STATUS:
        symptom = f"HTTP {doc.http_status} from a downstream call"
        return (symptom, text or symptom, host)
    if phrase := _GATEWAY_PHRASE_RE.search(text):
        return (f"downstream degradation ({phrase.group(1).lower()})", text, host)
    if status := _HTTP_STATUS_RE.search(text):
        return (f"HTTP {status.group(1)} from a downstream call", text, host)
    return None


def _has_fallback(docs: list[LogDoc], error_index: int | None) -> bool:
    """A working fallback usually shows up *after* the failure (retries, cache,
    circuit breaker), so scan the aftermath."""
    tail = docs[error_index:] if error_index is not None else docs
    return any(_FALLBACK_RE.search(_doc_text(doc)) for doc in tail)


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
async def third_party_probe(state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    """Scan the walked threads for outbound-call failure signatures and classify
    our-bug vs. third-party degradation. When a downstream failure is found, note
    whether a fallback absorbed it (this swings severity, PLAN.md §7)."""
    scan: list[tuple[list[LogDoc], int | None]] = [(t.docs, t.error_index) for t in state.threads]
    if not scan and state.broad_hits:
        scan = [(state.broad_hits, None)]

    for docs, error_index in scan:
        for doc in docs:
            match = _match_third_party(doc)
            if match is None:
                continue
            symptom, evidence, service = match
            had_fallback = _has_fallback(docs, error_index)
            finding = ThirdPartyFinding(
                involved=True,
                service=service,
                symptom=symptom,
                evidence=evidence[:500],
                had_fallback=had_fallback,
            )
            outcome = "fallback worked" if had_fallback else "no fallback"
            return {
                "third_party": finding,
                "_summary": f"third-party: {service or 'external'} — {symptom} ({outcome})",
            }

    return {
        "third_party": ThirdPartyFinding(involved=False),
        "_summary": "no third-party failure signature",
    }


def _sentry_query(state: InvestigationState) -> str:
    if state.signals and state.signals.exception_class:
        return state.signals.exception_class
    return state.error_text[:200]


@node("sentry_enrich", "Enriching from Sentry")
async def sentry_enrich(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Match the error to a Sentry issue and pull in-app frames, release SHA,
    culprit, and user count. Unconfigured/failing → ``skipped`` (run continues)."""
    clients = ctx.sentry_clients
    if not clients:
        return {
            "sentry": SentryFinding(skipped=True, skip_reason="no Sentry integration configured"),
            "_status": "skipped",
            "_summary": "no Sentry integration",
        }

    query = _sentry_query(state)
    errors: list[str] = []
    for client in clients:
        try:
            issues = await client.search_issues(query, limit=5)
        except Exception as exc:
            errors.append(str(exc))
            continue
        if not issues:
            continue
        top = issues[0]
        release: str | None = None
        frames = []
        try:
            event = await client.latest_event(top.id)
            release = event.release
            frames = event.frames
        except Exception as exc:
            errors.append(str(exc))
        finding = SentryFinding(
            issue_id=top.id,
            culprit=top.culprit,
            release=release,
            user_count=top.user_count,
            frames=frames,
        )
        in_app = sum(1 for f in frames if f.in_app)
        return {
            "sentry": finding,
            "_summary": f"issue {top.id}: {top.culprit or top.title} ({in_app} in-app frame(s))",
        }

    if errors and len(errors) >= len(clients):
        reason = "Sentry query failed: " + "; ".join(errors)
        return {
            "sentry": SentryFinding(skipped=True, skip_reason=reason),
            "_status": "skipped",
            "_summary": "Sentry unavailable",
        }
    return {
        "sentry": SentryFinding(skipped=True, skip_reason="no matching Sentry issue"),
        "_status": "skipped",
        "_summary": "no matching Sentry issue",
    }


@node("appdynamics_enrich", "Enriching from AppDynamics")
async def appdynamics_enrich(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Pull BT health, error rate, and exit-call breakdown for the window.
    Unconfigured/failing → ``skipped`` (run continues)."""
    clients = ctx.appdynamics_clients
    if not clients:
        return {
            "appdynamics": AppDFinding(
                skipped=True, skip_reason="no AppDynamics integration configured"
            ),
            "_status": "skipped",
            "_summary": "no AppDynamics integration",
        }

    window_mins = max(60, state.time_window_days * 24 * 60)
    errors: list[str] = []
    for client in clients:
        try:
            snapshots = await client.error_snapshots(duration_mins=window_mins)
        except Exception as exc:
            errors.append(str(exc))
            continue

        aggregated: dict[tuple[str, str | None], int] = {}
        for snap in snapshots:
            for call in snap.exit_calls:
                key = (call.target, call.call_type)
                aggregated[key] = aggregated.get(key, 0) + call.error_count
        exit_calls = [
            ExitCall(target=target, call_type=call_type, error_count=count)
            for (target, call_type), count in sorted(
                aggregated.items(), key=lambda kv: kv[1], reverse=True
            )
        ]

        error_rate: float | None = None
        try:
            values = await client.metric_data(
                "Overall Application Performance|Errors per Minute", duration_mins=window_mins
            )
            if values:
                error_rate = sum(values) / len(values)
        except Exception:  # noqa: S110 - the error-rate metric is optional enrichment
            pass

        finding = AppDFinding(
            bt_health="degraded" if snapshots else "healthy",
            error_rate=error_rate,
            exit_calls=exit_calls[:10],
        )
        return {
            "appdynamics": finding,
            "_summary": f"{len(snapshots)} error snapshot(s), {len(exit_calls)} exit call(s)",
        }

    reason = "AppDynamics query failed: " + "; ".join(errors)
    return {
        "appdynamics": AppDFinding(skipped=True, skip_reason=reason),
        "_status": "skipped",
        "_summary": "AppDynamics unavailable",
    }


# --- helpers (node 8: code_locate) ------------------------------------------
_CODE_SNIPPET_CONTEXT = 25
_MAX_CODE_LOCATIONS = 5
_GIT_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_MINIFIED_RE = re.compile(r"(\.min\.js|\.[0-9a-f]{8,}\.(?:js|css)|bundle\.js)$", re.IGNORECASE)

# Python: File "path", line N, in func
_PY_FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>\S+))?')
# Java/Kotlin: at pkg.Class.method(File.java:NN)
_JAVA_FRAME_RE = re.compile(
    r"at\s+(?P<module>[\w.$]+)\.(?P<func>[\w$<>]+)\((?P<file>[^():]+?)(?::(?P<line>\d+))?\)"
)
# JS/TS: at fn (path:line:col)  |  at path:line:col  (two colons distinguish from Java)
_JS_FRAME_RE = re.compile(
    r"at\s+(?:(?P<func>[\w.$<>\[\]]+)\s+)?\(?(?P<path>[^\s()]+\.[a-z]{1,4}):(?P<line>\d+):\d+\)?"
)


def _parse_stack_frames(text: str) -> list[Frame]:
    """Best-effort parse of a raw stack trace into frames (Python/Java/JS shapes).
    ``# TODO: verify against live log formats``."""
    frames: list[Frame] = []
    for m in _PY_FRAME_RE.finditer(text):
        frames.append(
            Frame(filename=m.group("path"), function=m.group("func"), lineno=int(m.group("line")))
        )
    for m in _JAVA_FRAME_RE.finditer(text):
        frames.append(
            Frame(
                filename=m.group("file"),
                module=m.group("module"),
                function=m.group("func"),
                lineno=int(m.group("line")) if m.group("line") else None,
            )
        )
    for m in _JS_FRAME_RE.finditer(text):
        frames.append(
            Frame(filename=m.group("path"), function=m.group("func"), lineno=int(m.group("line")))
        )
    return frames


def _matches_prefix(frame: Frame, prefix: str) -> bool:
    p = prefix.strip()
    if not p:
        return False
    pn_dot = p.replace("/", ".").strip(".")
    pn_slash = p.replace(".", "/").strip("/")
    for raw in (frame.module, frame.filename, frame.abs_path):
        if not raw:
            continue
        as_dot = raw.replace("/", ".")
        as_slash = raw.replace(".", "/").lstrip("/")
        if as_dot.startswith(pn_dot) or pn_dot in as_dot:
            return True
        if as_slash.startswith(pn_slash) or pn_slash in as_slash:
            return True
    return False


def _is_in_app(frame: Frame, repos: list[RepoRef]) -> bool:
    if frame.in_app:
        return True
    return any(_matches_prefix(frame, p) for repo in repos for p in repo.org_package_prefixes)


def _match_repo(frame: Frame, repos: list[RepoRef]) -> RepoRef | None:
    for repo in repos:
        if any(_matches_prefix(frame, p) for p in repo.org_package_prefixes):
            return repo
    return None


def _candidate_paths(frame: Frame) -> list[str]:
    """Repo-relative paths to try, most-specific first — tolerating monorepo
    leading segments and Java package→path layout (§6.5 path-prefix mismatch)."""
    paths: list[str] = []
    fn = frame.filename or frame.abs_path
    if fn and "/" in fn.lstrip("/"):
        p = fn.lstrip("/")
        parts = p.split("/")
        paths.extend("/".join(parts[i:]) for i in range(len(parts)))
    elif fn and frame.module and "." in frame.module:
        pkg = frame.module.rsplit(".", 1)[0].replace(".", "/")
        for root in ("", "src/main/java/", "src/main/kotlin/", "src/"):
            paths.append(f"{root}{pkg}/{fn}")
    elif frame.module:
        modpath = frame.module.replace(".", "/")
        for root, ext in (("", ".java"), ("src/main/java/", ".java"), ("src/", ".py")):
            paths.append(f"{root}{modpath}{ext}")
    elif fn:
        paths.append(fn.lstrip("/"))
    return list(dict.fromkeys(paths))


def _resolve_ref(sentry_release: str | None, repo: RepoRef) -> tuple[str, bool]:
    """(ref, from_sha). Prefer a git SHA in the Sentry release; else default branch."""
    if sentry_release and (m := _GIT_SHA_RE.search(sentry_release)):
        return m.group(0), True
    return repo.default_branch, False


def _extract_snippet(content: str, lineno: int | None) -> str | None:
    lines = content.splitlines()
    if not lines:
        return None
    if lineno is None:
        return "\n".join(lines[:_CODE_SNIPPET_CONTEXT])
    idx = min(max(0, lineno - 1), len(lines) - 1)
    start = max(0, idx - _CODE_SNIPPET_CONTEXT)
    end = min(len(lines), idx + _CODE_SNIPPET_CONTEXT + 1)
    return "\n".join(lines[start:end])


def _rank_locations(locations: list[CodeLocation]) -> list[CodeLocation]:
    # Stable: snippet-bearing + higher-confidence first, else stack order preserved.
    return sorted(
        locations, key=lambda c: (c.snippet is not None, c.confidence or 0.0), reverse=True
    )


async def _locate_frame(
    resolver: Any, frame: Frame, repo: RepoRef, release: str | None
) -> CodeLocation:
    ref, from_sha = _resolve_ref(release, repo)
    refs = list(dict.fromkeys([ref, repo.default_branch]))
    for cand in _candidate_paths(frame):
        for try_ref in refs:
            content = await resolver.fetch_file(repo.gitlab_project_id, cand, try_ref)
            if content is None:
                continue
            via_sha = from_sha and try_ref != repo.default_branch
            why = f"in-app frame in {repo.name}"
            if frame.function:
                why += f" ({frame.function})"
            why += f"; resolved at {'release SHA' if via_sha else 'default branch'} {try_ref}"
            return CodeLocation(
                repo=repo.name,
                path=cand,
                line=frame.lineno,
                ref=try_ref,
                snippet=_extract_snippet(content, frame.lineno),
                why=why,
                confidence=0.85 if via_sha else 0.6,
            )
    fallback_path = next(iter(_candidate_paths(frame)), frame.filename or frame.module or "")
    return CodeLocation(
        repo=repo.name,
        path=fallback_path,
        line=frame.lineno,
        ref=ref,
        snippet=None,
        why=f"file not found in {repo.name} at {ref} (or default branch)",
        confidence=0.25,
    )


def _gather_frames(state: InvestigationState) -> list[Frame]:
    frames: list[Frame] = []
    if state.sentry and not state.sentry.skipped:
        frames.extend(state.sentry.frames)
    texts = [state.error_text]
    texts.extend(d.stack_trace for t in state.threads for d in t.docs if d.stack_trace)
    for text in texts:
        frames.extend(_parse_stack_frames(text))
    return frames


@node("code_locate", "Locating code")
async def code_locate(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Frames (Sentry + parsed stack traces) → in-app filter (org prefixes) →
    repo + ref resolution → GitLab file → ±25-line snippet, top 5 ranked (§6.5)."""
    resolver = ctx.code_resolver
    if resolver is None:
        return {
            "code_locations": [],
            "_status": "skipped",
            "_summary": "no GitLab access configured",
        }

    repos = list(resolver.repos())
    release = state.sentry.release if state.sentry else None
    locations: list[CodeLocation] = []
    seen: set[tuple[str | None, int | None]] = set()

    for frame in _gather_frames(state):
        if not _is_in_app(frame, repos):
            continue  # vendor/stdlib — discard
        path_hint = frame.filename or frame.abs_path or frame.module or ""
        if _MINIFIED_RE.search(path_hint):
            continue  # minified/bundled FE frame — skip (source maps are Phase 2)
        key = (path_hint, frame.lineno)
        if key in seen:
            continue
        seen.add(key)

        repo = _match_repo(frame, repos)
        if repo is None:
            # In-app (per Sentry) but not in a registered repo → report path, no snippet.
            locations.append(
                CodeLocation(
                    repo="(unregistered)",
                    path=path_hint,
                    line=frame.lineno,
                    why="in-app frame but no registered repo matches its path",
                    confidence=0.2,
                )
            )
        else:
            locations.append(await _locate_frame(resolver, frame, repo, release))

        if len(locations) >= _MAX_CODE_LOCATIONS * 3:
            break  # enough candidates to rank; avoid unbounded GitLab calls

    ranked = _rank_locations(locations)[:_MAX_CODE_LOCATIONS]
    return {"code_locations": ranked, "_summary": f"{len(ranked)} code location(s)"}


# --- helpers (node 9: occurrence_stats) -------------------------------------
def _occurrence_signature(state: InvestigationState) -> str:
    if state.signals and state.signals.exception_class:
        return state.signals.exception_class
    return state.error_text[:200]


def _fill_series(counts: dict[str, int], *, end: date, days: int) -> list[DayBucket]:
    """A bucket for **every** day in the window (zero-filled), so the chart has no
    gaps (T-26)."""
    series: list[DayBucket] = []
    for offset in range(days - 1, -1, -1):
        iso = (end - timedelta(days=offset)).isoformat()
        series.append(DayBucket(date=iso, count=counts.get(iso, 0)))
    return series


@node("occurrence_stats", "Computing occurrence statistics")
async def occurrence_stats(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Daily occurrence histogram + distinct affected users + week-over-week delta
    (requirement 12.1). Queries two windows so WoW has a prior week to compare."""
    clients: Sequence[ElasticSearcher] = ctx.elastic_clients
    if not clients:
        return {
            "stats": OccurrenceStats(),
            "_status": "skipped",
            "_summary": "no Elastic app configured",
        }

    window = state.signals.time_window_days if state.signals else state.time_window_days
    span = window * 2  # need the prior week for the WoW delta
    signature = _occurrence_signature(state)

    counts: dict[str, int] = {}
    distinct_users = 0
    errors: list[str] = []
    for client in clients:
        try:
            buckets = await client.histogram(signature, window_days=span)
        except Exception as exc:
            errors.append(str(exc))
            continue
        for bucket in buckets:
            counts[bucket.date] = counts.get(bucket.date, 0) + bucket.count
        try:
            distinct_users += await client.cardinality(signature, window_days=window)
        except Exception as exc:
            errors.append(str(exc))

    if errors and len(errors) >= len(clients):
        return {
            "stats": OccurrenceStats(),
            "_status": "skipped",
            "_summary": "occurrence stats unavailable",
        }

    full = _fill_series(counts, end=datetime.now(UTC).date(), days=span)
    recent = full[-window:]
    this_week = sum(b.count for b in recent)
    prev_week = sum(b.count for b in full[:-window])
    wow_delta = (this_week - prev_week) / prev_week if prev_week else None

    stats = OccurrenceStats(series=recent, distinct_users=distinct_users, wow_delta=wow_delta)
    return {
        "stats": stats,
        "_summary": f"{this_week} occurrence(s)/{window}d · {distinct_users} user(s)",
    }


# --- helpers (nodes 10-11: synthesize + verify) -----------------------------
# Fixed headroom reserved out of ctx.max_tokens for instructions/schema/response
# so the evidence block itself never crowds out the model's own output budget.
_EVIDENCE_RESERVE_TOKENS = 4000
# ARCHITECTURE.md §6.1: verify may loop back to synthesize at most once.
MAX_VERIFY_RETRIES = 1
# If more than this fraction of cited claims turn out unsupported, the
# synthesis is judged too unreliable to salvage by dropping — retry once.
_DROP_RETRY_THRESHOLD = 0.5


class LLMJSONError(Exception):
    """Raised when the LLM still hasn't produced schema-valid JSON after the
    repair loop is exhausted. Carries the last raw output so the caller can
    store it (ARCHITECTURE.md §6.7: never fabricate — fail loudly instead)."""

    def __init__(self, raw_output: str, reason: str, attempts: int) -> None:
        super().__init__(
            f"LLM failed to produce valid JSON after {attempts} attempt(s): {reason}\n"
            f"raw output: {raw_output}"
        )
        self.raw_output = raw_output


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> dict[str, Any]:
    """Tolerate a model that wraps its JSON in prose or code fences."""
    try:
        result: dict[str, Any] = json.loads(text)
        return result
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            raise
        result = json.loads(match.group(0))
        return result


async def _call_llm_json[M: BaseModel](
    ctx: Any, system_prompt: str, user_prompt: str, schema_cls: type[M], *, max_repairs: int = 2
) -> tuple[M, int]:
    """One LLM call, validated against ``schema_cls`` with a JSON-repair retry
    loop (ARCHITECTURE.md §6.7): on invalid JSON/schema mismatch, feed the bad
    output + the validation error back and ask again, up to ``max_repairs``
    times. Returns the validated object and the total tokens spent."""
    if ctx.llm is None:
        raise RuntimeError("no LLM client configured")

    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_prompt),
    ]
    tokens = 0
    attempts = max_repairs + 1
    for attempt in range(attempts):
        resp = await ctx.llm.complete(
            messages, temperature=0.0, response_format={"type": "json_object"}
        )
        tokens += resp.usage.total_tokens
        try:
            data = _parse_json_object(resp.content)
            return schema_cls.model_validate(data), tokens
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt >= max_repairs:
                raise LLMJSONError(resp.content, str(exc), attempts) from exc
            messages = [
                *messages,
                LLMMessage(role="assistant", content=resp.content),
                LLMMessage(role="user", content=render("repair_v1.jinja2", error=str(exc))),
            ]
    raise AssertionError("unreachable")  # pragma: no cover


def _evidence_from_state(state: InvestigationState) -> list[EvidenceItem]:
    """Flatten every finding gathered so far into a numbered evidence list the
    LLM can cite by id. Order is oldest-thread-first, then the single-shot
    findings — this is also the order ``_fit_evidence_budget`` trims from."""
    items: list[EvidenceItem] = []
    for thread in state.threads:
        ts = _aware(thread.docs[0].timestamp) if thread.docs else None
        items.append(
            EvidenceItem(
                id=f"E{len(items) + 1}",
                source="elastic",
                ref=thread.txn_id,
                excerpt=thread.summary
                or _summarize_thread(
                    thread.txn_id, thread.docs, thread.error_index, thread.services
                ),
                ts=ts,
            )
        )
    if state.third_party and state.third_party.involved:
        excerpt = (
            f"{state.third_party.symptom} "
            f"(service={state.third_party.service or 'unknown'}, "
            f"fallback={'yes' if state.third_party.had_fallback else 'no'})"
        )
        items.append(
            EvidenceItem(
                id=f"E{len(items) + 1}", source="elastic", ref="third_party_probe", excerpt=excerpt
            )
        )
    if state.sentry and not state.sentry.skipped:
        excerpt = (
            f"culprit={state.sentry.culprit or 'unknown'}; "
            f"release={state.sentry.release or 'unknown'}; "
            f"users={state.sentry.user_count if state.sentry.user_count is not None else 'unknown'}"
        )
        items.append(
            EvidenceItem(
                id=f"E{len(items) + 1}",
                source="sentry",
                ref=state.sentry.issue_id or "sentry",
                excerpt=excerpt,
            )
        )
    if state.appdynamics and not state.appdynamics.skipped:
        top = ", ".join(f"{c.target}({c.error_count})" for c in state.appdynamics.exit_calls[:5])
        excerpt = (
            f"bt_health={state.appdynamics.bt_health or 'unknown'}; "
            f"error_rate={state.appdynamics.error_rate}; "
            f"top exit calls: {top or 'none'}"
        )
        items.append(
            EvidenceItem(
                id=f"E{len(items) + 1}",
                source="appdynamics",
                ref="appdynamics_enrich",
                excerpt=excerpt,
            )
        )
    for loc in state.code_locations:
        excerpt = loc.why or "(no detail)"
        if loc.snippet:
            excerpt += "\n" + _truncate_tokens(loc.snippet, 150)
        items.append(
            EvidenceItem(
                id=f"E{len(items) + 1}",
                source="code",
                ref=f"{loc.repo}:{loc.path}:{loc.line if loc.line is not None else '?'}",
                excerpt=excerpt,
            )
        )
    if state.stats and (state.stats.series or state.stats.distinct_users):
        excerpt = (
            f"{state.stats.distinct_users} distinct user(s) affected; "
            f"week-over-week delta={state.stats.wow_delta}"
        )
        items.append(
            EvidenceItem(
                id=f"E{len(items) + 1}", source="elastic", ref="occurrence_stats", excerpt=excerpt
            )
        )
    return items


def _evidence_tokens(item: EvidenceItem) -> int:
    return max(1, len(item.excerpt) // 4)  # ~4 chars/token heuristic, matches _truncate_tokens


def _fit_evidence_budget(
    items: list[EvidenceItem], budget_tokens: int
) -> tuple[list[EvidenceItem], int]:
    """Truncate evidence oldest-first until it fits ``budget_tokens``
    (ARCHITECTURE.md §6.7). Dated items (thread evidence) are dropped oldest to
    newest; undated single-shot findings are treated as "current" and dropped
    last, only if the dated items alone still don't fit."""
    total = sum(_evidence_tokens(i) for i in items)
    if total <= budget_tokens or not items:
        return items, 0

    dated = sorted((i for i in items if i.ts is not None), key=lambda i: i.ts)  # type: ignore[arg-type,return-value]
    undated = [i for i in items if i.ts is None]
    drop_order = [*dated, *undated]

    kept_ids = {i.id for i in items}
    dropped = 0
    for item in drop_order:
        if total <= budget_tokens:
            break
        kept_ids.discard(item.id)
        total -= _evidence_tokens(item)
        dropped += 1

    return [i for i in items if i.id in kept_ids], dropped


_SEVERITY_ORDER: list[SeverityLevel] = ["LOW", "MEDIUM", "HIGH", "BLOCKER"]


def _clamp_severity(proposed: SeverityLevel, baseline: SeverityLevel) -> SeverityLevel:
    """The LLM may move severity by at most one level from the deterministic
    score (ARCHITECTURE.md §6.6); anything further is clamped back."""
    p, b = _SEVERITY_ORDER.index(proposed), _SEVERITY_ORDER.index(baseline)
    if abs(p - b) <= 1:
        return proposed
    return _SEVERITY_ORDER[b + (1 if p > b else -1)]


def _confidence_penalty(state: InvestigationState) -> float:
    """Confidence is lowered — deterministically, not by asking the LLM to
    self-assess — when evidence is thin or an integration was skipped."""
    penalty = 0.0
    if not state.sentry or state.sentry.skipped:
        penalty += 0.1
    if not state.appdynamics or state.appdynamics.skipped:
        penalty += 0.1
    if not state.code_locations:
        penalty += 0.05
    if len(state.threads) == 0:
        penalty += 0.2
    elif len(state.threads) == 1:
        penalty += 0.05
    return penalty


# --- scoring + synthesis ----------------------------------------------------
@node("severity_score", "Scoring severity")
async def severity_score(_state: InvestigationState, _ctx: Any) -> dict[str, Any]:
    return {"severity_score": SeverityScore(severity="LOW")}


@node("synthesize", "Synthesizing the result", critical=True)
async def synthesize(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Single LLM call over all gathered evidence -> the result schema
    (PLAN.md §5.2). Zero evidence never reaches the LLM: it returns a clear
    'insufficient data' result instead of risking a fabricated guess
    (ARCHITECTURE.md §6.7)."""
    evidence = _evidence_from_state(state)
    if not evidence:
        return {
            "result": InvestigationResult(
                severity="LOW",
                confidence=0.0,
                root_cause=None,
                open_questions=[
                    "No evidence was gathered for this investigation — insufficient "
                    "data to determine a root cause."
                ],
            ),
            "_summary": "insufficient evidence — no LLM call made",
        }

    budget = max(500, ctx.max_tokens - _EVIDENCE_RESERVE_TOKENS)
    evidence, dropped = _fit_evidence_budget(evidence, budget)

    skip_notes: list[str] = []
    if state.sentry and state.sentry.skipped and state.sentry.skip_reason:
        skip_notes.append(f"Sentry: {state.sentry.skip_reason}")
    if state.appdynamics and state.appdynamics.skipped and state.appdynamics.skip_reason:
        skip_notes.append(f"AppDynamics: {state.appdynamics.skip_reason}")

    signals = state.signals or QuerySignals(time_window_days=state.time_window_days)
    score = state.severity_score or SeverityScore()

    system_prompt = render("synthesize_system_v1.jinja2")
    user_prompt = render(
        "synthesize_user_v1.jinja2",
        error_text=state.error_text,
        signals=signals,
        severity_score=score,
        evidence=[e.model_dump() for e in evidence],
        evidence_truncated=dropped or None,
        skip_notes=skip_notes,
        retry_note=(
            "Note: a previous synthesis included citations the evidence did not "
            "actually support. Only cite an evidence id whose excerpt directly "
            "substantiates the claim next to it."
            if state.verify_retries
            else None
        ),
    )

    try:
        parsed, tokens = await _call_llm_json(ctx, system_prompt, user_prompt, SynthesizeOutput)
    except LLMJSONError as exc:
        raise RuntimeError(str(exc)) from exc

    penalty = _confidence_penalty(state)
    confidence = round(max(0.0, min(1.0, parsed.confidence - penalty)), 2)
    severity = _clamp_severity(parsed.severity, score.severity)

    open_questions = list(parsed.open_questions)
    for note in skip_notes:
        if note not in open_questions:
            open_questions.append(note)
    if dropped:
        open_questions.append(
            f"{dropped} evidence item(s) were truncated (oldest-first) to fit the "
            f"{ctx.max_tokens}-token budget."
        )

    result = InvestigationResult(
        severity=severity,
        severity_rationale=parsed.severity_rationale,
        confidence=confidence,
        root_cause=parsed.root_cause,
        root_cause_evidence=[c.model_dump() for c in parsed.root_cause_evidence],
        code_locations=parsed.code_locations,
        suggested_fixes=[f.model_dump() for f in parsed.suggested_fixes],
        third_party_involved=parsed.third_party_involved,
        third_party_details=parsed.third_party_details,
        open_questions=open_questions,
    )
    return {
        "result": result,
        "tokens_used": tokens,
        "_summary": (
            f"{severity} · confidence {confidence:.2f} · "
            f"{len(result.root_cause_evidence)} citation(s)"
        ),
    }


@node("verify", "Verifying claims")
async def verify(state: InvestigationState, ctx: Any) -> dict[str, Any]:
    """Second LLM pass: check each cited claim is actually backed by the
    evidence it names; drop unsupported claims and lower confidence
    accordingly. When too many claims turn out unsupported, loop back to
    `synthesize` once (ARCHITECTURE.md §6.1)."""
    result = state.result
    if result is None or not result.root_cause_evidence:
        return {"verify_retry_needed": False, "_summary": "nothing to verify"}

    evidence = _evidence_from_state(state)
    known_ids = {e.id for e in evidence}
    citations = [
        {"ref": c.get("ref"), "excerpt": str(c.get("excerpt") or "")[:200]}
        for c in result.root_cause_evidence
    ]

    try:
        parsed, tokens = await _call_llm_json(
            ctx,
            render("verify_system_v1.jinja2"),
            render(
                "verify_user_v1.jinja2",
                evidence=[e.model_dump() for e in evidence],
                root_cause=result.root_cause or "",
                citations=citations,
            ),
            VerifyOutput,
        )
    except LLMJSONError:
        # Verification is best-effort: fall back to a syntactic check (the
        # cited id must exist) rather than failing a non-critical node.
        parsed = VerifyOutput(
            citations=[
                VerifyCitation(ref=str(c["ref"]), supported=c["ref"] in known_ids)
                for c in citations
            ]
        )
        tokens = 0

    supported_ids = {c.ref for c in parsed.citations if c.supported and c.ref in known_ids}
    kept = [c for c in result.root_cause_evidence if c.get("ref") in supported_ids]
    dropped = len(result.root_cause_evidence) - len(kept)
    drop_ratio = dropped / len(result.root_cause_evidence)

    if drop_ratio > _DROP_RETRY_THRESHOLD and state.verify_retries < MAX_VERIFY_RETRIES:
        return {
            "verify_retries": state.verify_retries + 1,
            "verify_retry_needed": True,
            "tokens_used": tokens,
            "_summary": (
                f"{dropped}/{len(result.root_cause_evidence)} claim(s) unsupported; "
                "retrying synthesis"
            ),
        }

    confidence = round(max(0.0, result.confidence - 0.1 * dropped), 2)
    open_questions = list(result.open_questions)
    for note in parsed.notes:
        if note not in open_questions:
            open_questions.append(note)
    if dropped:
        msg = f"{dropped} claim(s) were dropped for lacking supporting evidence."
        if msg not in open_questions:
            open_questions.append(msg)

    updated = result.model_copy(
        update={
            "root_cause_evidence": kept,
            "confidence": confidence,
            "open_questions": open_questions,
        }
    )
    return {
        "result": updated,
        "verify_retry_needed": False,
        "tokens_used": tokens,
        "_summary": f"{len(kept)}/{len(result.root_cause_evidence)} claim(s) verified",
    }


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

"""Runtime context injected into graph nodes (T-20, T-21).

Kept free of ``db``/``workers``/``integrations``: the worker builds this and passes
it via the LangGraph ``config``. Nodes emit steps through the callbacks, read
timeouts here, and search logs through the injected :class:`ElasticSearcher`
ports (structurally satisfied by ``integrations.elastic.ElasticClient``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from langchain_core.runnables import RunnableConfig

from taproot.core.models import (
    AppDErrorSnapshot,
    BroadSearchResult,
    DayBucket,
    LogDoc,
    RepoRef,
    SentryEventDetail,
    SentryIssue,
)

# (node, title) -> seq
EmitStart = Callable[[str, str], Awaitable[int]]
# (seq, node, status, summary)
EmitFinish = Callable[[int, str, str, str | None], Awaitable[None]]


@runtime_checkable
class ElasticSearcher(Protocol):
    """Port for the per-app Elasticsearch client the agent reasons over."""

    async def search(
        self, error_text: str, *, window_days: int = 7, size: int = 50
    ) -> BroadSearchResult: ...

    async def thread(self, transaction_id: str, *, size: int = 500) -> list[LogDoc]: ...

    async def histogram(self, signature: str, *, window_days: int = 7) -> list[DayBucket]: ...

    async def cardinality(
        self, signature: str, *, window_days: int = 7, field: str = "user_name"
    ) -> int: ...


@runtime_checkable
class SentrySearcher(Protocol):
    """Port for the per-app Sentry client (T-24), satisfied by ``SentryClient``."""

    async def search_issues(self, query: str, *, limit: int = 10) -> list[SentryIssue]: ...

    async def latest_event(self, issue_id: str) -> SentryEventDetail: ...


@runtime_checkable
class AppDynamicsProbe(Protocol):
    """Port for the per-app AppDynamics client (T-24), satisfied by ``AppDynamicsClient``."""

    async def error_snapshots(self, *, duration_mins: int = 60) -> list[AppDErrorSnapshot]: ...

    async def metric_data(self, metric_path: str, *, duration_mins: int = 60) -> list[float]: ...


@runtime_checkable
class CodeResolver(Protocol):
    """Port for ``code_locate`` (T-25): the project's registered repos plus a
    read-only GitLab file fetch. Satisfied by ``workers.code_resolver``."""

    def repos(self) -> Sequence[RepoRef]: ...

    async def fetch_file(self, gitlab_project_id: int, path: str, ref: str) -> str | None: ...


@dataclass
class AgentContext:
    emit_start: EmitStart
    emit_finish: EmitFinish
    node_timeout_s: float = 45.0
    max_duration_s: float = 300.0
    max_threads: int = 5
    # One client per app (ADR-0002: each app has its own index / Sentry account /
    # AppD app). Typed as covariant Sequences so concrete integration clients
    # assign cleanly without list-invariance friction.
    elastic_clients: Sequence[ElasticSearcher] = ()
    sentry_clients: Sequence[SentrySearcher] = ()
    appdynamics_clients: Sequence[AppDynamicsProbe] = ()
    # Optional: absent when the project has no GitLab access (code_locate skips).
    code_resolver: CodeResolver | None = None


CONFIG_KEY = "ctx"


def ctx_from_config(config: RunnableConfig) -> AgentContext:
    ctx = (config.get("configurable") or {}).get(CONFIG_KEY)
    if not isinstance(ctx, AgentContext):  # pragma: no cover - misconfiguration guard
        raise RuntimeError("AgentContext missing from graph config")
    return ctx

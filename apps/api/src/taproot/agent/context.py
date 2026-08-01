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

from taproot.core.models import BroadSearchResult, LogDoc

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


@dataclass
class AgentContext:
    emit_start: EmitStart
    emit_finish: EmitFinish
    node_timeout_s: float = 45.0
    max_duration_s: float = 300.0
    max_threads: int = 5
    # One searcher per app (ADR-0002: each app has its own index). Typed as a
    # covariant Sequence so concrete clients (integrations.ElasticClient) assign
    # cleanly without list-invariance friction.
    elastic_clients: Sequence[ElasticSearcher] = ()


CONFIG_KEY = "ctx"


def ctx_from_config(config: RunnableConfig) -> AgentContext:
    ctx = (config.get("configurable") or {}).get(CONFIG_KEY)
    if not isinstance(ctx, AgentContext):  # pragma: no cover - misconfiguration guard
        raise RuntimeError("AgentContext missing from graph config")
    return ctx

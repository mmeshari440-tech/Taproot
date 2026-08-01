"""Runtime context injected into graph nodes (T-20).

Kept free of ``db``/``workers``: the worker builds this from its ``StepRecorder``
and passes it via the LangGraph ``config``. Nodes emit steps through these
callbacks and read timeouts from here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig

# (node, title) -> seq
EmitStart = Callable[[str, str], Awaitable[int]]
# (seq, node, status, summary)
EmitFinish = Callable[[int, str, str, str | None], Awaitable[None]]


@dataclass
class AgentContext:
    emit_start: EmitStart
    emit_finish: EmitFinish
    node_timeout_s: float = 45.0
    max_duration_s: float = 300.0


CONFIG_KEY = "ctx"


def ctx_from_config(config: RunnableConfig) -> AgentContext:
    ctx = (config.get("configurable") or {}).get(CONFIG_KEY)
    if not isinstance(ctx, AgentContext):  # pragma: no cover - misconfiguration guard
        raise RuntimeError("AgentContext missing from graph config")
    return ctx

"""Normalized domain models returned by integration clients (ARCHITECTURE.md §3).

Integration clients never leak raw provider JSON — they return these models, so
the agent and services reason over every provider with the same shapes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ConnectionTestResult(BaseModel):
    """Outcome of an integration ``test_connection()`` call."""

    ok: bool
    latency_ms: int
    detail: str | None = None
    error: str | None = None


class GitLabGroup(BaseModel):
    id: int
    name: str
    full_path: str
    web_url: str | None = None


class GitLabProject(BaseModel):
    id: int
    name: str
    path_with_namespace: str
    default_branch: str = "main"
    web_url: str | None = None
    topics: list[str] = []


class GitLabFile(BaseModel):
    path: str
    ref: str
    content: str


class RepoRef(BaseModel):
    """A registered project repo, flattened for the agent's ``code_locate`` node
    (T-25) so it can resolve frames → repo/ref without touching ``db``."""

    name: str
    gitlab_project_id: int
    default_branch: str = "main"
    kind: str = "OTHER"  # FE | BE | OTHER
    org_package_prefixes: list[str] = []
    web_url: str | None = None


# --- Elasticsearch (T-13) ---------------------------------------------------
class LogDoc(BaseModel):
    """One normalized log document. Field mapping assumes the documented ELK
    schema; `# TODO: verify against live instance` (ARCHITECTURE.md §12)."""

    timestamp: datetime | None = None
    severity: str | None = None
    message: str | None = None
    stack_trace: str | None = None
    service: str | None = None
    transaction_id: str | None = None
    user_name: str | None = None
    http_status: int | None = None
    url: str | None = None


class BroadSearchResult(BaseModel):
    docs: list[LogDoc] = []
    transaction_ids: list[str] = []


class DayBucket(BaseModel):
    date: str  # ISO date (YYYY-MM-DD)
    count: int


# --- Sentry (T-14) ----------------------------------------------------------
class Frame(BaseModel):
    """Shared stack frame — Sentry and stack_trace parsing normalize to this."""

    filename: str | None = None
    function: str | None = None
    lineno: int | None = None
    module: str | None = None
    abs_path: str | None = None
    in_app: bool = False


class SentryIssue(BaseModel):
    id: str
    title: str
    culprit: str | None = None
    level: str | None = None
    count: int | None = None
    user_count: int | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    permalink: str | None = None


class SentryEventDetail(BaseModel):
    event_id: str
    release: str | None = None  # feeds code_locate ref resolution (T-25)
    frames: list[Frame] = []
    tags: dict[str, str] = {}


# --- AppDynamics (T-15) -----------------------------------------------------
class BusinessTransaction(BaseModel):
    id: int
    name: str
    tier_name: str | None = None


class ExitCall(BaseModel):
    target: str
    call_type: str | None = None  # HTTP, JDBC, …
    error_count: int = 0


class AppDErrorSnapshot(BaseModel):
    id: str | None = None
    error_message: str | None = None
    exit_calls: list[ExitCall] = []


# --- LLM (T-16) -------------------------------------------------------------
class LLMMessage(BaseModel):
    role: str  # system | user | assistant
    content: str


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    content: str
    usage: LLMUsage = LLMUsage()

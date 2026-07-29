"""Normalized domain models returned by integration clients (ARCHITECTURE.md §3).

Integration clients never leak raw provider JSON — they return these models, so
the agent and services reason over every provider with the same shapes.
"""

from __future__ import annotations

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

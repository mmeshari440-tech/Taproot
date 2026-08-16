"""Agent domain schemas (ARCHITECTURE.md §6.2, §5.2).

These are the per-node findings and the final result contract. Severity is a
plain ``Literal`` (not the DB enum) so ``agent`` stays free of ``db`` — the worker
maps it when persisting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from taproot.core.models import DayBucket, ExitCall, Frame, LogDoc

SeverityLevel = Literal["BLOCKER", "HIGH", "MEDIUM", "LOW"]
EvidenceSource = Literal["elastic", "sentry", "appdynamics", "code"]


class QuerySignals(BaseModel):
    exception_class: str | None = None
    tokens: list[str] = []
    service_hint: str | None = None
    time_window_days: int = 7
    search_variants: list[str] = []


class LogThread(BaseModel):
    txn_id: str
    docs: list[LogDoc] = []
    error_index: int | None = None
    services: list[str] = []
    user_hash: str | None = None
    summary: str | None = None


class ThirdPartyFinding(BaseModel):
    involved: bool = False
    service: str | None = None
    symptom: str | None = None
    evidence: str | None = None
    had_fallback: bool = False


class SentryFinding(BaseModel):
    issue_id: str | None = None
    culprit: str | None = None
    release: str | None = None
    user_count: int | None = None
    frames: list[Frame] = []
    skipped: bool = False
    skip_reason: str | None = None


class AppDFinding(BaseModel):
    bt_health: str | None = None
    error_rate: float | None = None
    exit_calls: list[ExitCall] = []
    skipped: bool = False
    skip_reason: str | None = None


class CodeLocation(BaseModel):
    repo: str
    path: str
    line: int | None = None
    ref: str | None = None
    snippet: str | None = None
    why: str | None = None
    confidence: float | None = None


class OccurrenceStats(BaseModel):
    series: list[DayBucket] = []
    distinct_users: int = 0
    wow_delta: float | None = None


class SeverityScore(BaseModel):
    score: int = 0
    severity: SeverityLevel = "LOW"
    breakdown: dict[str, int] = {}


class InvestigationResult(BaseModel):
    severity: SeverityLevel = "LOW"
    severity_rationale: str | None = None
    confidence: float = 0.0
    root_cause: str | None = None
    root_cause_evidence: list[dict[str, Any]] = []
    code_locations: list[CodeLocation] = []
    suggested_fixes: list[dict[str, Any]] = []
    third_party_involved: bool = False
    third_party_details: dict[str, Any] | None = None
    open_questions: list[str] = []


# --- synthesize / verify (T-27) ----------------------------------------------
class EvidenceItem(BaseModel):
    """One item of gathered evidence, given a stable id (``E1``, ``E2``, …) so the
    LLM can cite it and ``verify`` can check the citation is real. ``ts`` is used
    only to order thread evidence for oldest-first token-budget truncation — it
    never reaches the prompt."""

    id: str
    source: EvidenceSource
    ref: str
    excerpt: str
    ts: datetime | None = None


class EvidenceCitation(BaseModel):
    """A claim's supporting evidence, per the schema in ``PLAN.md`` §5.2. ``ref``
    must be the id of an :class:`EvidenceItem` (e.g. ``"E3"``) — this is how
    ``verify`` checks the citation is real."""

    source: EvidenceSource
    ref: str
    excerpt: str


class SuggestedFix(BaseModel):
    title: str
    description: str
    diff: str | None = None
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    effort: Literal["S", "M", "L"]


class SynthesizeOutput(BaseModel):
    """Strict validation target for the ``synthesize`` LLM call — the schema in
    ``PLAN.md`` §5.2. A field that fails to validate (missing, wrong shape, an
    invalid literal) triggers the JSON repair loop."""

    severity: SeverityLevel
    severity_rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    root_cause: str = Field(min_length=1)
    root_cause_evidence: list[EvidenceCitation] = []
    code_locations: list[CodeLocation] = []
    suggested_fixes: list[SuggestedFix] = []
    third_party_involved: bool = False
    third_party_details: dict[str, Any] | None = None
    open_questions: list[str] = []


class VerifyCitation(BaseModel):
    ref: str
    supported: bool
    reason: str | None = None


class VerifyOutput(BaseModel):
    """Strict validation target for the ``verify`` LLM call: one supported/not
    verdict per cited claim, plus any caveats worth surfacing."""

    citations: list[VerifyCitation] = []
    notes: list[str] = []

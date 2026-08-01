"""The investigation state threaded through every graph node (ARCHITECTURE.md §6.2).

Nodes return **partial** updates; they never mutate in place. The two bookkeeping
fields carry reducers so the parallel nodes (5–9) can all touch them without a
write conflict; every other field is written by exactly one node (disjoint).
"""

from __future__ import annotations

import operator
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel

from taproot.agent.schemas import (
    AppDFinding,
    CodeLocation,
    InvestigationResult,
    LogThread,
    OccurrenceStats,
    QuerySignals,
    SentryFinding,
    SeverityScore,
    ThirdPartyFinding,
)
from taproot.core.models import LogDoc


def merge_errors(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    return {**left, **right}


class InvestigationState(BaseModel):
    # inputs (immutable)
    investigation_id: UUID
    project_id: UUID
    error_text: str
    time_window_days: int = 7

    # node 1
    signals: QuerySignals | None = None

    # nodes 2–4
    broad_hits: list[LogDoc] = []
    candidate_txn_ids: list[str] = []
    threads: list[LogThread] = []

    # nodes 5–9 (parallel — each writes only its own field)
    third_party: ThirdPartyFinding | None = None
    sentry: SentryFinding | None = None
    appdynamics: AppDFinding | None = None
    code_locations: list[CodeLocation] = []
    stats: OccurrenceStats | None = None

    # scoring + synthesis
    severity_score: SeverityScore | None = None
    result: InvestigationResult | None = None

    # bookkeeping (reducers → safe under parallel writes)
    node_errors: Annotated[dict[str, str], merge_errors] = {}
    tokens_used: Annotated[int, operator.add] = 0
    verify_retries: int = 0

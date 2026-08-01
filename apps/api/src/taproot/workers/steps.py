"""Step recorder (T-18): persist each step to Postgres, THEN publish to the bus.

DB-first ordering is what makes `Last-Event-ID` replay correct on reconnect
(ARCHITECTURE.md §5.2). The recorder owns the per-investigation `seq` counter.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.events import EventBus
from taproot.db.models import InvestigationStep, StepStatus


class StepRecorder:
    def __init__(
        self, session: AsyncSession, event_bus: EventBus, investigation_id: UUID
    ) -> None:
        self._session = session
        self._bus = event_bus
        self._inv_id = investigation_id
        self._seq = 0
        # Parallel nodes (5–9) emit concurrently; serialize DB writes + seq.
        self._lock = asyncio.Lock()

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def _next_seq(self) -> int:
        if self._seq == 0:
            current = (
                await self._session.execute(
                    select(func.max(InvestigationStep.seq)).where(
                        InvestigationStep.investigation_id == self._inv_id
                    )
                )
            ).scalar_one_or_none()
            self._seq = int(current or 0)
        self._seq += 1
        return self._seq

    async def _emit(self, seq: int | None, event: dict[str, Any]) -> None:
        # Persisted before published — replay relies on this ordering.
        await self._session.commit()
        await self._bus.publish(self._inv_id, event)

    async def start(self, node: str, title: str) -> int:
        async with self._lock:
            seq = await self._next_seq()
            self._session.add(
                InvestigationStep(
                    investigation_id=self._inv_id,
                    seq=seq,
                    node=node,
                    title=title,
                    status=StepStatus.running,
                    started_at=datetime.now(UTC),
                )
            )
            await self._emit(
                seq, {"type": "step.start", "seq": seq, "node": node, "title": title}
            )
            return seq

    async def finish(
        self,
        seq: int,
        node: str,
        *,
        status: StepStatus = StepStatus.ok,
        summary: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            step = (
                await self._session.execute(
                    select(InvestigationStep).where(
                        InvestigationStep.investigation_id == self._inv_id,
                        InvestigationStep.seq == seq,
                    )
                )
            ).scalar_one()
            step.status = status
            step.summary = summary
            step.finished_at = datetime.now(UTC)
            await self._emit(
                seq,
                {
                    "type": "step.finish",
                    "seq": seq,
                    "node": node,
                    "status": status.value,
                    "summary": summary,
                    "metrics": metrics or {},
                },
            )

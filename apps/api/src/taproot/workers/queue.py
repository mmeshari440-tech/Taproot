"""Job queue abstraction (T-17).

The API depends on the :class:`JobQueue` protocol, not on ARQ directly, so it is
testable with :class:`FakeJobQueue` and the transport can change without touching
the API layer.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

RUN_INVESTIGATION = "run_investigation"


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue_investigation(self, investigation_id: UUID) -> None: ...


class ArqJobQueue:
    """Enqueues onto ARQ via an ``arq.ArqRedis``-compatible pool."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def enqueue_investigation(self, investigation_id: UUID) -> None:
        await self._pool.enqueue_job(RUN_INVESTIGATION, str(investigation_id))


class FakeJobQueue:
    """Records enqueued ids — used in tests."""

    def __init__(self) -> None:
        self.enqueued: list[UUID] = []

    async def enqueue_investigation(self, investigation_id: UUID) -> None:
        self.enqueued.append(investigation_id)

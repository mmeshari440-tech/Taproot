"""ARQ task definitions (T-17).

The worker is the only writer of the ``investigation_*`` tables (ARCHITECTURE.md
§2). The real LangGraph run is wired in Sprint 3 (T-20) via the injectable
``runner``; here the run body is a stub so the QUEUED→RUNNING→DONE lifecycle,
cancellation, and retry-then-FAILED semantics can be built and tested now.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.config import get_settings
from taproot.core.events import EventBus, InMemoryEventBus, RedisEventBus
from taproot.core.logging import get_logger
from taproot.db.models import Investigation, InvestigationStatus, StepStatus
from taproot.db.session import get_sessionmaker
from taproot.workers.steps import StepRecorder

_log = get_logger(__name__)

# A runner performs the work, emitting steps via the recorder. Sprint 3 injects
# the LangGraph agent; the default is a no-op stub.
Runner = Callable[[Investigation, StepRecorder], Awaitable[None]]


async def _stub_runner(_investigation: Investigation, _recorder: StepRecorder) -> None:
    return None


async def demo_runner(investigation: Investigation, recorder: StepRecorder) -> None:
    """Sprint-2 placeholder that streams a couple of fake steps so the SSE UI can
    be built end-to-end. Replaced by the LangGraph agent in Sprint 3 (T-20)."""
    for node, title in [
        ("normalize_query", "Normalizing the error"),
        ("elastic_broad_search", "Searching Elasticsearch"),
    ]:
        seq = await recorder.start(node, title)
        await recorder.finish(seq, node, status=StepStatus.ok, summary=f"{node} complete")


async def execute_investigation(
    session: AsyncSession,
    investigation_id: UUID,
    *,
    job_try: int = 1,
    max_tries: int = 2,
    runner: Runner | None = None,
    event_bus: EventBus | None = None,
) -> None:
    """Drive one investigation through its lifecycle.

    On failure: re-raise to let ARQ retry until ``job_try >= max_tries``, then
    mark ``FAILED`` (never requeued forever). Cancellation set before/while the
    run is honored. Terminal transitions publish a final ``done`` (or ``error``)
    event so live SSE subscribers can close.
    """
    bus = event_bus or InMemoryEventBus()
    inv = await session.get(Investigation, investigation_id)
    if inv is None:
        _log.warning("investigation_missing", investigation_id=str(investigation_id))
        return
    if inv.status == InvestigationStatus.CANCELLED:
        return

    recorder = StepRecorder(session, bus, investigation_id)
    started = datetime.now(UTC)
    inv.status = InvestigationStatus.RUNNING
    inv.started_at = started
    await session.commit()

    try:
        await (runner or _stub_runner)(inv, recorder)
    except Exception as exc:
        if job_try >= max_tries:
            inv.status = InvestigationStatus.FAILED
            inv.error = str(exc)
            inv.finished_at = datetime.now(UTC)
            await session.commit()
            await bus.publish(
                investigation_id,
                {"type": "error", "message": str(exc), "recoverable": False},
            )
            await bus.publish(investigation_id, {"type": "done"})
            _log.error("investigation_failed", investigation_id=str(investigation_id))
            return
        await session.rollback()
        raise  # let ARQ retry

    # Honor a cancel that arrived during the run.
    await session.refresh(inv)
    if inv.status == InvestigationStatus.CANCELLED:
        await bus.publish(investigation_id, {"type": "done"})
        return

    finished = datetime.now(UTC)
    inv.status = InvestigationStatus.DONE
    inv.finished_at = finished
    # Use the local aware timestamps (DB may return naive on SQLite).
    inv.duration_ms = int((finished - started).total_seconds() * 1000)
    await session.commit()
    await bus.publish(investigation_id, {"type": "done"})


def _build_event_bus() -> EventBus:
    from redis.asyncio import from_url

    return RedisEventBus(from_url(get_settings().redis_url))  # type: ignore[no-untyped-call]


async def run_investigation(ctx: dict[str, Any], investigation_id: str) -> None:
    """ARQ entry point."""
    maker = get_sessionmaker()
    async with maker() as session:
        await execute_investigation(
            session,
            UUID(investigation_id),
            job_try=int(ctx.get("job_try", 1)),
            max_tries=2,
            runner=demo_runner,
            event_bus=_build_event_bus(),
        )


def _redis_settings() -> Any:
    from arq.connections import RedisSettings

    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    """ARQ worker config: ``arq taproot.workers.tasks.WorkerSettings``."""

    functions: ClassVar[list[Any]] = [run_investigation]
    max_tries = 2
    redis_settings = _redis_settings()

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.events import InMemoryEventBus
from taproot.db.models import (
    Investigation,
    InvestigationResult,
    InvestigationStatus,
    InvestigationStep,
    Project,
    Severity,
)
from taproot.workers.steps import StepRecorder
from taproot.workers.tasks import agent_runner, execute_investigation


async def _queued(session: AsyncSession) -> Investigation:
    project = Project(name="P", slug=f"p-{id(session)}")
    session.add(project)
    await session.flush()
    inv = Investigation(project_id=project.id, error_text="boom", status=InvestigationStatus.QUEUED)
    session.add(inv)
    await session.commit()
    return inv


async def test_stub_run_completes(session: AsyncSession) -> None:
    inv = await _queued(session)
    await execute_investigation(session, inv.id)
    await session.refresh(inv)
    assert inv.status == InvestigationStatus.DONE
    assert inv.started_at is not None and inv.finished_at is not None
    assert inv.duration_ms is not None


async def test_precancelled_is_left_alone(session: AsyncSession) -> None:
    inv = await _queued(session)
    inv.status = InvestigationStatus.CANCELLED
    await session.commit()
    await execute_investigation(session, inv.id)
    await session.refresh(inv)
    assert inv.status == InvestigationStatus.CANCELLED


async def test_cancel_during_run_is_honored(session: AsyncSession) -> None:
    inv = await _queued(session)

    async def cancelling_runner(investigation: Investigation, _recorder: StepRecorder) -> None:
        investigation.status = InvestigationStatus.CANCELLED
        await session.commit()

    await execute_investigation(session, inv.id, runner=cancelling_runner)
    await session.refresh(inv)
    assert inv.status == InvestigationStatus.CANCELLED


async def test_retry_then_fail(session: AsyncSession) -> None:
    inv = await _queued(session)

    async def failing_runner(_investigation: Investigation, _recorder: StepRecorder) -> None:
        raise RuntimeError("worker crashed")

    # First attempt re-raises to let ARQ retry (not yet FAILED).
    with pytest.raises(RuntimeError):
        await execute_investigation(session, inv.id, job_try=1, max_tries=2, runner=failing_runner)
    await session.refresh(inv)
    assert inv.status == InvestigationStatus.RUNNING

    # Final attempt marks FAILED, never requeued forever.
    await execute_investigation(session, inv.id, job_try=2, max_tries=2, runner=failing_runner)
    await session.refresh(inv)
    assert inv.status == InvestigationStatus.FAILED
    assert inv.error is not None and "crashed" in inv.error


async def test_agent_runner_streams_nodes_and_persists_result(session: AsyncSession) -> None:
    inv = await _queued(session)
    await execute_investigation(
        session, inv.id, runner=agent_runner, event_bus=InMemoryEventBus()
    )
    await session.refresh(inv)
    assert inv.status == InvestigationStatus.DONE

    step_count = (
        await session.execute(
            select(func.count())
            .select_from(InvestigationStep)
            .where(InvestigationStep.investigation_id == inv.id)
        )
    ).scalar_one()
    # The seeded project has no verified Elastic app → broad search finds nothing
    # → the run routes straight to synthesis: normalize, search, synthesize, verify.
    assert step_count == 4

    result = (
        await session.execute(
            select(InvestigationResult).where(InvestigationResult.investigation_id == inv.id)
        )
    ).scalar_one()
    assert result.severity == Severity.LOW

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.events import InMemoryEventBus
from taproot.db.models import Investigation, InvestigationStep, Project, StepStatus
from taproot.workers.steps import StepRecorder


async def test_in_memory_bus_roundtrip() -> None:
    bus = InMemoryEventBus()
    inv_id = uuid4()
    async with bus.subscribe(inv_id) as stream:
        await bus.publish(inv_id, {"type": "done"})
        event = await stream.__anext__()
    assert event == {"type": "done"}


async def test_step_recorder_persists_then_publishes(session: AsyncSession) -> None:
    project = Project(name="P", slug=f"p-{id(session)}")
    session.add(project)
    await session.flush()
    inv = Investigation(project_id=project.id, error_text="x")
    session.add(inv)
    await session.commit()

    bus = InMemoryEventBus()
    published: list[dict] = []
    async with bus.subscribe(inv.id) as stream:
        recorder = StepRecorder(session, bus, inv.id)
        seq = await recorder.start("normalize_query", "Normalizing")
        published.append(await stream.__anext__())
        await recorder.finish(seq, "normalize_query", status=StepStatus.ok, summary="done")
        published.append(await stream.__anext__())

    # Persisted (DB is source of truth for replay)...
    steps = (
        await session.execute(
            select(InvestigationStep).where(InvestigationStep.investigation_id == inv.id)
        )
    ).scalars().all()
    assert len(steps) == 1
    assert steps[0].seq == 1
    assert steps[0].status == StepStatus.ok
    # ...and published.
    assert published[0]["type"] == "step.start"
    assert published[1]["type"] == "step.finish"
    assert published[1]["summary"] == "done"

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

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.agent.context import AgentContext, CodeResolver
from taproot.agent.graph import run_graph
from taproot.agent.schemas import InvestigationResult as AgentResult
from taproot.agent.schemas import OccurrenceStats
from taproot.agent.state import InvestigationState
from taproot.core.config import Settings, get_settings
from taproot.core.events import EventBus, InMemoryEventBus, RedisEventBus
from taproot.core.logging import get_logger
from taproot.core.secrets import SecretStore, build_secret_store
from taproot.db.models import (
    Investigation,
    InvestigationResult,
    InvestigationStatus,
    Severity,
    StepStatus,
)
from taproot.db.session import get_sessionmaker
from taproot.integrations.gitlab import GitLabClient
from taproot.services import integration_service, project_service
from taproot.workers.code_resolver import GitLabCodeResolver
from taproot.workers.steps import StepRecorder

_log = get_logger(__name__)

# A runner performs the work, emitting steps via the recorder.
Runner = Callable[[Investigation, StepRecorder], Awaitable[None]]


async def _stub_runner(_investigation: Investigation, _recorder: StepRecorder) -> None:
    return None


async def _persist_result(
    recorder: StepRecorder, investigation: Investigation, final: dict[str, Any]
) -> None:
    investigation.token_usage = {"total": int(final.get("tokens_used", 0))}
    raw = final.get("result")
    if raw is None:
        return
    # `astream` keeps nested models as instances; coerce dicts too, for safety.
    result = raw if isinstance(raw, AgentResult) else AgentResult(**raw)

    series: list[dict[str, Any]] | None = None
    stats_raw = final.get("stats")
    if stats_raw is not None:
        stats = (
            stats_raw if isinstance(stats_raw, OccurrenceStats) else OccurrenceStats(**stats_raw)
        )
        series = [b.model_dump() for b in stats.series]

    recorder.session.add(
        InvestigationResult(
            investigation_id=investigation.id,
            severity=Severity(result.severity),
            severity_rationale=result.severity_rationale,
            confidence=result.confidence,
            root_cause=result.root_cause,
            root_cause_evidence=result.root_cause_evidence,
            code_locations=[c.model_dump() for c in result.code_locations],
            suggested_fixes=result.suggested_fixes,
            third_party_involved=result.third_party_involved,
            third_party_details=result.third_party_details,
            occurrence_series=series,
            raw_model_output=result.model_dump(),
        )
    )


def _build_secret_store(settings: Settings) -> SecretStore:
    vault_client = None
    if settings.secret_store == "vault":  # noqa: S105  (store kind, not a secret)
        import hvac

        vault_client = hvac.Client(url=settings.vault_addr, token=settings.vault_token)
    return build_secret_store(
        kind=settings.secret_store,
        env=settings.env,
        local_secret_key=settings.local_secret_key,
        vault_client=vault_client,
    )


async def agent_runner(investigation: Investigation, recorder: StepRecorder) -> None:
    """Run the LangGraph agent. Injects one read-only Elastic client per verified
    app (ADR-0002) so the deep-dive nodes (T-21) have data to reason over."""
    settings = get_settings()

    async def emit_start(node: str, title: str) -> int:
        return await recorder.start(node, title)

    async def emit_finish(seq: int, node: str, status: str, summary: str | None) -> None:
        await recorder.finish(seq, node, status=StepStatus(status), summary=summary)

    http_client = httpx.AsyncClient(timeout=30.0)
    try:
        secret_store = _build_secret_store(settings)
        project_id = investigation.project_id
        elastic_clients = await integration_service.elastic_clients_for_project(
            recorder.session, project_id, secret_store=secret_store, http_client=http_client
        )
        sentry_clients = await integration_service.sentry_clients_for_project(
            recorder.session, project_id, secret_store=secret_store, http_client=http_client
        )
        appdynamics_clients = await integration_service.appdynamics_clients_for_project(
            recorder.session, project_id, secret_store=secret_store, http_client=http_client
        )

        code_resolver: CodeResolver | None = None
        if settings.gitlab_url and settings.gitlab_token:
            repo_refs = await project_service.repo_refs_for_project(recorder.session, project_id)
            if repo_refs:
                code_resolver = GitLabCodeResolver(
                    GitLabClient(
                        settings.gitlab_url, settings.gitlab_token, http_client=http_client
                    ),
                    repo_refs,
                )

        ctx = AgentContext(
            emit_start=emit_start,
            emit_finish=emit_finish,
            node_timeout_s=settings.agent_node_timeout_s,
            max_duration_s=settings.agent_max_duration_s,
            elastic_clients=elastic_clients,
            sentry_clients=sentry_clients,
            appdynamics_clients=appdynamics_clients,
            code_resolver=code_resolver,
        )
        state = InvestigationState(
            investigation_id=investigation.id,
            project_id=investigation.project_id,
            error_text=investigation.error_text,
            time_window_days=investigation.time_window_days,
        )
        final = await run_graph(state, ctx)
        await _persist_result(recorder, investigation, final)
    finally:
        await http_client.aclose()


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
            runner=agent_runner,
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

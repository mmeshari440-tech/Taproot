"""OpenTelemetry setup (ARCHITECTURE.md §8.5).

Tracing is optional: install the ``otel`` extra and set ``TAPROOT_OTEL_ENABLED=true``
plus ``TAPROOT_OTEL_EXPORTER_OTLP_ENDPOINT``. When the extra is not installed or
telemetry is disabled, :func:`setup_telemetry` is a safe no-op — the app still runs.

The exporter points at the customer's own Elastic: Taproot dogfoods the thing it
monitors. One trace spans api -> redis -> worker -> every node -> every external call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from taproot.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

    from taproot.core.config import Settings

_log = get_logger(__name__)


def setup_telemetry(app: FastAPI, settings: Settings) -> None:
    """Instrument the FastAPI app if telemetry is enabled and available."""
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _log.warning(
            "otel_enabled but the 'otel' extra is not installed; tracing disabled. "
            "Install with: pip install '.[otel]'"
        )
        return

    resource = Resource.create({"service.name": settings.service_name})
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    _log.info("telemetry_configured", endpoint=settings.otel_exporter_otlp_endpoint)

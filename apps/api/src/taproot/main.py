"""FastAPI application factory.

``api`` owns AuthN/Z, CRUD, validation, job enqueue, and SSE relay. It must not
run the agent or make long-running calls (ARCHITECTURE.md §2).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from taproot import __version__
from taproot.api.v1 import api_router
from taproot.core.config import get_settings
from taproot.core.logging import RequestContextMiddleware, configure_logging, get_logger
from taproot.core.otel import setup_telemetry

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _log.info("startup", version=__version__)
    yield
    _log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Taproot API",
        version=__version__,
        summary="On-prem AI root-cause investigation",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    setup_telemetry(app, settings)
    app.include_router(api_router)
    return app


app = create_app()

"""Structured JSON logging (ARCHITECTURE.md §8.5, TASKS.md T-05).

* JSON log lines with ``request_id``, ``user_sub`` and ``investigation_id`` bound
  from context.
* A redaction processor guarantees no secret-shaped string reaches a log record.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp
from structlog.typing import EventDict, WrappedLogger

from taproot.core.redaction import redact_secrets


def _redact_processor(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Scrub secret-shaped substrings from every string value in the event."""
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact_secrets(value)
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog + stdlib logging to emit redacted JSON lines."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=level, force=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def bind_request_context(**kwargs: str) -> None:
    """Bind values (e.g. ``user_sub``, ``investigation_id``) to the log context."""
    structlog.contextvars.bind_contextvars(**kwargs)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign/propagate a request id and bind it to the log context."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response: Response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = request_id
        return response

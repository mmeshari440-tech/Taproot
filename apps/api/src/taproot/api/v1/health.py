"""Health endpoint (T-01).

Liveness only for now. T-17/T-34 extend readiness to check DB + Redis reachable
(ARCHITECTURE.md §9).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from taproot import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)

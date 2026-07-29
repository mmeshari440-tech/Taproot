"""Version 1 of the HTTP API.

`/health` stays at the root (probes); versioned endpoints live under `/api/v1`.
"""

from __future__ import annotations

from fastapi import APIRouter

from taproot.api.v1.auth import router as auth_router
from taproot.api.v1.health import router as health_router
from taproot.api.v1.integrations import router as integrations_router
from taproot.api.v1.projects import router as projects_router

api_router = APIRouter()
api_router.include_router(health_router)  # GET /health

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(auth_router)  # /api/v1/me, /api/v1/admin/ping
v1_router.include_router(projects_router)  # /api/v1/projects...
v1_router.include_router(integrations_router)  # /api/v1/projects/{id}/integrations...
api_router.include_router(v1_router)

__all__ = ["api_router"]

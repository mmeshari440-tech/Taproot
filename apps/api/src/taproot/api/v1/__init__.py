"""Version 1 of the HTTP API."""

from __future__ import annotations

from fastapi import APIRouter

from taproot.api.v1.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)

__all__ = ["api_router"]

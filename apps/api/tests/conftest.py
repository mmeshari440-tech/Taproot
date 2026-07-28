"""Shared test fixtures.

Environment defaults are set before any ``taproot`` import so ``get_settings()``
succeeds without a real Postgres. Model tests run against in-memory SQLite.
"""

from __future__ import annotations

import os

os.environ.setdefault("TAPROOT_ENV", "development")
os.environ.setdefault("TAPROOT_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("TAPROOT_LOCAL_SECRET_KEY", "dev-test-key")

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from taproot.db import models  # noqa: F401  (register tables)
from taproot.db.base import Base


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()

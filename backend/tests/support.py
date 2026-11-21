"""Shared helpers for integration tests (live Postgres required)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.db.rls import ensure_app_role, rls_dsn_from_admin


def admin_dsn() -> str:
    """The admin/owner DSN integration tests run against."""
    return os.environ["IC_DATABASE__URL"]


@asynccontextmanager
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    """A disposable admin engine (superuser in dev — bypasses RLS)."""
    engine = create_async_engine(admin_dsn())
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def rls_engine() -> AsyncIterator[AsyncEngine]:
    """A disposable engine on the non-superuser app role (RLS enforced).

    Bootstraps the role via the admin connection first, so a fresh database
    works without manual steps.
    """
    async with admin_engine() as admin:
        await ensure_app_role(admin)
    engine = create_async_engine(rls_dsn_from_admin(admin_dsn()))
    try:
        yield engine
    finally:
        await engine.dispose()


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Plain session factory over an engine."""
    return async_sessionmaker(engine, expire_on_commit=False)

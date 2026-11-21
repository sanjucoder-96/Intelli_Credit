"""Async engines and session factories with lazy process-wide singletons.

Two engines, two trust levels (ADR-0013):

- the **admin/owner engine** (``database.url``) serves migrations, the
  pre-authentication paths (login, refresh), and system jobs; in dev it is
  the compose superuser and bypasses RLS by design;
- the **RLS engine** (``database.rls_url``, the non-superuser application
  role) serves tenant traffic: :func:`tenant_session` opens a transaction and
  pins ``app.current_org_id`` with ``SET LOCAL``, so Postgres row-level
  security enforces isolation *underneath* repository scoping — leakage needs
  two simultaneous bugs.

The FastAPI dependency :func:`get_session` provides commit-on-success /
rollback-on-error semantics per request. Celery workers (Phase 4) build their
own engine per process via :func:`create_engine_and_factory`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import DatabaseConfig, get_settings
from app.core.context import TenantContext
from app.core.errors import ServiceUnavailableError

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_rls_engine: AsyncEngine | None = None
_rls_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine_and_factory(
    cfg: DatabaseConfig,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build a fresh engine + session factory from configuration."""
    engine = create_async_engine(
        cfg.url,
        echo=cfg.echo,
        pool_size=cfg.pool_size,
        max_overflow=cfg.max_overflow,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


def get_engine() -> AsyncEngine:
    """Return the lazily created process-wide engine."""
    global _engine, _session_factory
    if _engine is None:
        _engine, _session_factory = create_engine_and_factory(get_settings().database)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the lazily created process-wide session factory."""
    global _session_factory
    if _session_factory is None:
        get_engine()
        assert _session_factory is not None  # set by get_engine()
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, commit/rollback managed.

    Commits when the request handler returns normally; rolls back on any
    exception before re-raising it to the error handlers.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


def get_rls_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the lazily created session factory on the RLS (app-role) engine.

    Raises :class:`ServiceUnavailableError` when ``IC_DATABASE__RLS_URL`` is
    not configured — tenant traffic must never silently fall back to the
    policy-bypassing admin engine.
    """
    global _rls_engine, _rls_session_factory
    if _rls_session_factory is None:
        rls_url = get_settings().database.rls_url
        if not rls_url:
            raise ServiceUnavailableError(
                "IC_DATABASE__RLS_URL is not configured; tenant sessions require "
                "the row-level-security application role (ADR-0013)"
            )
        cfg = get_settings().database
        _rls_engine = create_async_engine(
            rls_url,
            echo=cfg.echo,
            pool_size=cfg.pool_size,
            max_overflow=cfg.max_overflow,
            pool_pre_ping=True,
        )
        _rls_session_factory = async_sessionmaker(_rls_engine, expire_on_commit=False)
    return _rls_session_factory


@asynccontextmanager
async def tenant_session(ctx: TenantContext) -> AsyncIterator[AsyncSession]:
    """A transaction on the RLS engine pinned to ``ctx.org_id``.

    ``SET LOCAL`` scopes the GUC to this transaction only — pooled connections
    can never leak one tenant's identity into another's query. Commit on
    success, rollback on error, exactly like :func:`get_session`.
    """
    factory = get_rls_session_factory()
    async with factory() as session:
        try:
            async with session.begin():
                # set_config(..., is_local := true) == SET LOCAL, but accepts
                # bind parameters (plain SET takes only literals).
                await session.execute(
                    sa.select(sa.func.set_config("app.current_org_id", str(ctx.org_id), True))
                )
                yield session
        except BaseException:
            # session.begin() has already rolled back; re-raise for handlers.
            raise


async def dispose_engine() -> None:
    """Dispose the singleton engines (application shutdown)."""
    global _engine, _session_factory, _rls_engine, _rls_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
    if _rls_engine is not None:
        await _rls_engine.dispose()
        _rls_engine = None
        _rls_session_factory = None


async def ping_database() -> bool:
    """Readiness probe: ``SELECT 1`` against the configured database."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
        return True
    except Exception:
        return False

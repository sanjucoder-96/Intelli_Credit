"""Generic repository base — THE tenant-scoping choke point (ADR-0013).

:meth:`BaseRepository.scoped` is the single place where ``org_id`` narrowing
and soft-delete exclusion are applied. Every repository SELECT — including
deliberately cross-org lookups like the login path, which pass ``ctx=None`` —
must be built through it, so "forgot the WHERE org_id" cannot happen one
repository at a time. The tenant context is per call (repositories are
constructed in the DI graph before authentication resolves), matching the
application-port signatures. Phase 3 adds Postgres row-level security as the
independent second isolation layer behind this one.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.infrastructure.db.base import UUIDPrimaryKeyMixin


class BaseRepository[ModelT: UUIDPrimaryKeyMixin]:
    """Session-bound repository generic over one ORM model.

    Subclasses set the ``model`` class attribute and build every statement
    via :meth:`scoped`.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @classmethod
    def scoped(cls, stmt: Select[Any], ctx: TenantContext | None) -> Select[Any]:
        """Apply tenant scoping and soft-delete exclusion — the choke point.

        With a :class:`TenantContext`, statements over org-carrying models are
        narrowed to that organization. ``ctx=None`` is reserved for the
        pre-authentication flows (login, token refresh) and still applies
        soft-delete exclusion, so even deliberately cross-org queries route
        through this one function.
        """
        org_column = getattr(cls.model, "org_id", None)
        if org_column is not None and ctx is not None:
            stmt = stmt.where(org_column == ctx.org_id)
        deleted_column = getattr(cls.model, "deleted_at", None)
        if deleted_column is not None:
            stmt = stmt.where(deleted_column.is_(None))
        return stmt

    async def get_row(self, ctx: TenantContext | None, entity_id: UUID) -> ModelT | None:
        """Fetch one row by primary key within ``ctx`` scope."""
        stmt = self.scoped(sa.select(self.model).where(self.model.id == entity_id), ctx)
        result = await self._session.execute(stmt)
        row: ModelT | None = result.scalar_one_or_none()
        return row

    def add(self, instance: ModelT) -> ModelT:
        """Stage a new instance on the session and return it."""
        self._session.add(instance)
        return instance

    async def flush(self) -> None:
        """Flush pending changes so database defaults/IDs become visible."""
        await self._session.flush()

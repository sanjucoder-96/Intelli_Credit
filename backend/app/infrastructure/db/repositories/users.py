"""User repository implementing :class:`app.application.ports.UserRepositoryPort`.

Every statement is built through :meth:`BaseRepository.scoped` — the tenant
choke point (ADR-0013). The login/refresh paths are deliberately cross-org
(``ctx=None``; they run before any :class:`TenantContext` exists, and the org
claim is derived from the returned row) but still route through the choke
point so soft-delete exclusion is uniform. Phase 4 adds admin management
methods (create, deactivate, role change) with audit logging.
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa

from app.application.ports import UserRow
from app.core.context import TenantContext
from app.infrastructure.db.models.identity import User
from app.infrastructure.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Async SQLAlchemy adapter for user persistence."""

    model = User

    async def get_by_email(self, email: str) -> UserRow | None:
        """Cross-org lookup by unique email (login path; ``ctx=None``)."""
        stmt = self.scoped(sa.select(User).where(User.email == email), None)
        result = await self._session.execute(stmt)
        row: User | None = result.scalar_one_or_none()
        return UserRow.model_validate(row) if row is not None else None

    async def get(self, ctx: TenantContext, user_id: UUID) -> UserRow | None:
        """Tenant-scoped lookup by primary key."""
        row = await self.get_row(ctx, user_id)
        return UserRow.model_validate(row) if row is not None else None

    async def get_by_id(self, user_id: UUID) -> UserRow | None:
        """Cross-org lookup by primary key (token refresh/introspection only)."""
        row = await self.get_row(None, user_id)
        return UserRow.model_validate(row) if row is not None else None

    async def touch_last_login(self, user_id: UUID) -> None:
        """Record a successful login timestamp (server clock, UTC)."""
        stmt = sa.update(User).where(User.id == user_id).values(last_login_at=sa.func.now())
        await self._session.execute(stmt)

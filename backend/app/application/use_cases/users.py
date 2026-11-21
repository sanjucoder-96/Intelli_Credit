"""User self-service use cases.

Phase 2 ships only the current-user lookup backing ``GET /users/me``.
Phase 4 adds admin user management (invite, deactivate, role change) with
audit logging.
"""

from __future__ import annotations

from app.application.ports import UserRepositoryPort
from app.core.context import TenantContext
from app.core.errors import NotFoundError
from app.domain.entities import UserEntity


class GetCurrentUser:
    """Return the acting user's profile from the tenant context."""

    def __init__(self, users: UserRepositoryPort) -> None:
        self._users = users

    async def execute(self, ctx: TenantContext) -> UserEntity:
        """Load ``ctx.user_id`` within ``ctx.org_id`` scope.

        Raises :class:`NotFoundError` if the token subject no longer exists
        in the organization (deleted between token issuance and use).
        """
        user = await self._users.get(ctx, ctx.user_id)
        if user is None:
            raise NotFoundError("User not found")
        return UserEntity.model_validate(user)

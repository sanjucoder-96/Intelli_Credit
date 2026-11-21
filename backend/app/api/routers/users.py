"""User self-service endpoints.

Phase 2 ships ``GET /users/me`` only; Phase 4 adds admin user management
guarded by ``require_permissions(Permission.USERS_MANAGE)``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.providers import get_tenant_context, get_user_repository
from app.api.schemas.users import UserResponse
from app.application.use_cases.users import GetCurrentUser
from app.core.context import TenantContext
from app.infrastructure.db.repositories.users import UserRepository

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def me(
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserResponse:
    """Return the authenticated user's profile."""
    entity = await GetCurrentUser(users).execute(ctx)
    return UserResponse.model_validate(entity)

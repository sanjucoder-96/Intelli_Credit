"""The dependency-injection composition root (ADR-0001).

Every concrete adapter is bound to its port here and nowhere else; routers
depend only on these provider functions, and tests override them via
``app.dependency_overrides``. Phase 3+ providers (storage, retriever, LLM
gateway) are added in this module as their adapters land.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.use_cases.auth import AuthenticateUser, RefreshTokens
from app.core.config import Settings, get_settings
from app.core.context import TenantContext, current_request_id
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.logging import bind_request_context
from app.core.security import PasswordHasher, TokenService
from app.domain.entities import UserEntity
from app.domain.enums import ROLE_PERMISSIONS, Permission, Role
from app.infrastructure.db.engine import get_session, tenant_session
from app.infrastructure.db.repositories.users import UserRepository
from app.infrastructure.flags import CachedFlagService
from app.infrastructure.redis.client import get_redis
from app.infrastructure.storage.local import LocalFilesystemStorage

bearer_scheme = HTTPBearer(auto_error=False)
"""Bearer-token extractor; ``auto_error=False`` so missing credentials raise
our own :class:`UnauthorizedError` envelope instead of FastAPI's default."""

_password_hasher = PasswordHasher()


def get_settings_dep() -> Settings:
    """Provide application settings (cached process-wide)."""
    return get_settings()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provide the request-scoped database session (commit/rollback managed)."""
    async for session in get_session():
        yield session


def get_redis_dep() -> Any:
    """Provide the process-wide async Redis client.

    Typed loosely because the redis asyncio client is generic over its
    response decoding; consumers treat it as ``redis.asyncio.Redis``.
    """
    return get_redis()


def get_password_hasher() -> PasswordHasher:
    """Provide the argon2 password hasher (stateless singleton)."""
    return _password_hasher


def get_token_service(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TokenService:
    """Provide the JWT issue/verify service."""
    return TokenService(settings.auth)


def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserRepository:
    """Provide the user repository bound to the request session."""
    return UserRepository(session)


def get_authenticate_user(
    users: Annotated[UserRepository, Depends(get_user_repository)],
    hasher: Annotated[PasswordHasher, Depends(get_password_hasher)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthenticateUser:
    """Provide the login use case wired to its ports."""
    return AuthenticateUser(
        users, hasher, tokens, access_ttl_seconds=settings.auth.access_ttl_seconds
    )


def get_refresh_tokens(
    users: Annotated[UserRepository, Depends(get_user_repository)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> RefreshTokens:
    """Provide the token-refresh use case wired to its ports."""
    return RefreshTokens(users, tokens, access_ttl_seconds=settings.auth.access_ttl_seconds)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserEntity:
    """Authenticate the request: decode the access token and load its user.

    Raises :class:`UnauthorizedError` for a missing/invalid/expired token, a
    refresh token presented as an access token, or a deleted/deactivated
    subject. On success the org/user IDs are bound to the log context so every
    subsequent line carries them.
    """
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")

    payload = tokens.decode(credentials.credentials, expected_type="access")
    user = await users.get_by_id(payload.sub)
    if user is None or not user.is_active:
        raise UnauthorizedError("Invalid credentials")

    bind_request_context(current_request_id.get() or "-", org_id=user.org_id, user_id=user.id)
    return UserEntity.model_validate(user)


def get_tenant_context(
    user: Annotated[UserEntity, Depends(get_current_user)],
) -> TenantContext:
    """Derive the tenant context every repository call requires (ADR-0013)."""
    return TenantContext(org_id=user.org_id, user_id=user.id, role=user.role)


async def get_tenant_db_session(
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> AsyncIterator[AsyncSession]:
    """RLS-bound session pinned to the caller's org (the second layer).

    Authenticated business endpoints use THIS session — repository scoping
    plus Postgres row-level security, per ADR-0013. Pre-auth endpoints
    (login/refresh) stay on :func:`get_db_session` (deliberately cross-org).
    """
    async with tenant_session(ctx) as session:
        yield session


def get_storage(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> LocalFilesystemStorage:
    """Provide the blob-storage adapter (ADR-0016)."""
    return LocalFilesystemStorage(settings.storage.root)


def get_flag_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CachedFlagService:
    """Provide the Redis-cached feature-flag service (ADR-0015).

    Rides the admin session: global flag rows (org NULL) must stay readable
    and writable through this service regardless of tenant scope.
    """
    return CachedFlagService(session, get_redis())


def require_roles(
    *roles: Role,
) -> Callable[[UserEntity], Coroutine[Any, Any, UserEntity]]:
    """Build a dependency that admits only the given roles.

    Raises :class:`ForbiddenError` (403 envelope) for authenticated users
    whose role is not listed.
    """
    allowed = frozenset(roles)

    async def dependency(
        user: Annotated[UserEntity, Depends(get_current_user)],
    ) -> UserEntity:
        if user.role not in allowed:
            raise ForbiddenError(
                "Role not permitted for this operation",
                details={"required_roles": sorted(role.value for role in allowed)},
            )
        return user

    return dependency


def require_permissions(
    *permissions: Permission,
) -> Callable[[UserEntity], Coroutine[Any, Any, UserEntity]]:
    """Build a dependency that requires every listed permission.

    Permissions are resolved through :data:`ROLE_PERMISSIONS` — the single
    source of truth for role grants.
    """
    required = frozenset(permissions)

    async def dependency(
        user: Annotated[UserEntity, Depends(get_current_user)],
    ) -> UserEntity:
        granted = ROLE_PERMISSIONS.get(user.role, frozenset())
        missing = required - granted
        if missing:
            raise ForbiddenError(
                "Missing required permissions",
                details={"missing_permissions": sorted(perm.value for perm in missing)},
            )
        return user

    return dependency

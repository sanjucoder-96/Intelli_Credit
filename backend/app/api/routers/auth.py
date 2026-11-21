"""Authentication endpoints: credential login and refresh exchange.

Bad credentials surface as a 401 envelope with code ``unauthorized`` (raised
by the use case, translated by ``app.api.errors``). Login outcomes — success
AND failure — write audit rows (ADR-0013); the failure row is keyed to the
attempted email's org when the account exists, so brute-force attempts
against real accounts are visible to that tenant's admins.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.providers import (
    get_authenticate_user,
    get_db_session,
    get_refresh_tokens,
    get_user_repository,
)
from app.api.schemas.auth import LoginRequest, RefreshRequest, TokenPairResponse
from app.application.use_cases.auth import AuthenticateUser, RefreshTokens
from app.core.errors import UnauthorizedError
from app.infrastructure.db.repositories.audit import record_audit
from app.infrastructure.db.repositories.users import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    use_case: Annotated[AuthenticateUser, Depends(get_authenticate_user)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenPairResponse:
    """Exchange email/password for an access + refresh token pair."""
    client_ip = request.client.host if request.client else None
    try:
        pair = await use_case.execute(body.email, body.password)
    except UnauthorizedError:
        known = await users.get_by_email(body.email)
        if known is not None:
            await record_audit(
                session,
                org_id=known.org_id,
                actor_id=None,
                action="auth.login_failed",
                entity_type="user",
                entity_id=known.id,
                ip=client_ip,
            )
        raise

    actor = await users.get_by_email(body.email)
    if actor is not None:
        await record_audit(
            session,
            org_id=actor.org_id,
            actor_id=actor.id,
            action="auth.login",
            entity_type="user",
            entity_id=actor.id,
            ip=client_ip,
        )
    return TokenPairResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type=pair.token_type,
        expires_in=pair.expires_in,
    )


@router.post("/refresh")
async def refresh(
    body: RefreshRequest,
    use_case: Annotated[RefreshTokens, Depends(get_refresh_tokens)],
) -> TokenPairResponse:
    """Exchange a valid refresh token for a fresh token pair."""
    pair = await use_case.execute(body.refresh_token)
    return TokenPairResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type=pair.token_type,
        expires_in=pair.expires_in,
    )

"""Authentication use cases: credential login and refresh-token exchange.

Auth plumbing per ADR-0013 — not business logic. Phase 4 adds audit-log rows
on login events and refresh-token rotation with revocation.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.application.ports import PasswordHasherPort, TokenServicePort, UserRepositoryPort
from app.core.errors import UnauthorizedError


class TokenPair(BaseModel):
    """Access + refresh token pair returned by the auth use cases."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthenticateUser:
    """Verify email/password credentials and issue a token pair."""

    def __init__(
        self,
        users: UserRepositoryPort,
        hasher: PasswordHasherPort,
        tokens: TokenServicePort,
        *,
        access_ttl_seconds: int,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._tokens = tokens
        self._access_ttl_seconds = access_ttl_seconds

    async def execute(self, email: str, password: str) -> TokenPair:
        """Authenticate and return tokens.

        Raises :class:`UnauthorizedError` with an identical message for
        unknown email, inactive account, and wrong password so responses do
        not leak which factor failed.
        """
        user = await self._users.get_by_email(email)
        if user is None or not user.is_active:
            raise UnauthorizedError("Invalid credentials")
        if not self._hasher.verify(user.hashed_password, password):
            raise UnauthorizedError("Invalid credentials")

        await self._users.touch_last_login(user.id)
        return TokenPair(
            access_token=self._tokens.create_access_token(
                sub=user.id, org=user.org_id, role=user.role.value
            ),
            refresh_token=self._tokens.create_refresh_token(sub=user.id),
            expires_in=self._access_ttl_seconds,
        )


class RefreshTokens:
    """Exchange a valid refresh token for a fresh token pair."""

    def __init__(
        self,
        users: UserRepositoryPort,
        tokens: TokenServicePort,
        *,
        access_ttl_seconds: int,
    ) -> None:
        self._users = users
        self._tokens = tokens
        self._access_ttl_seconds = access_ttl_seconds

    async def execute(self, refresh_token: str) -> TokenPair:
        """Validate the refresh token, reload the user, re-issue both tokens.

        The user is reloaded so a deactivated account cannot mint new access
        tokens for the remainder of its refresh window.
        """
        payload = self._tokens.decode(refresh_token, expected_type="refresh")
        user = await self._users.get_by_id(payload.sub)
        if user is None or not user.is_active:
            raise UnauthorizedError("Invalid credentials")

        return TokenPair(
            access_token=self._tokens.create_access_token(
                sub=user.id, org=user.org_id, role=user.role.value
            ),
            refresh_token=self._tokens.create_refresh_token(sub=user.id),
            expires_in=self._access_ttl_seconds,
        )

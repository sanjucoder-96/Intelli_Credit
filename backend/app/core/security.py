"""Password hashing (argon2) and JWT issuance/verification (ADR-0013).

Access tokens carry ``org`` and ``role`` claims for tenancy enforcement;
refresh tokens carry only the subject. Phase 4 adds refresh-token rotation
with a Redis-backed denylist keyed by ``jti``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher as _Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel

from app.core.config import AuthConfig
from app.core.errors import UnauthorizedError

TokenType = Literal["access", "refresh"]


class TokenPayload(BaseModel):
    """Validated claims of a decoded IntelliCredit JWT."""

    sub: UUID
    org: UUID | None = None
    role: str | None = None
    type: TokenType
    exp: int
    iat: int
    jti: str


class PasswordHasher:
    """Argon2id password hashing with constant-time verification."""

    def __init__(self) -> None:
        self._hasher = _Argon2Hasher()

    def hash(self, password: str) -> str:
        """Hash a plaintext password into an argon2id encoded string."""
        return self._hasher.hash(password)

    def verify(self, hashed: str, password: str) -> bool:
        """Return True iff ``password`` matches ``hashed``.

        Returns False (never raises) on mismatch or a malformed/invalid hash
        so callers can treat every failure identically.
        """
        try:
            return self._hasher.verify(hashed, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


class TokenService:
    """Issues and verifies access/refresh JWTs from :class:`AuthConfig`."""

    def __init__(self, config: AuthConfig) -> None:
        self._config = config

    def create_access_token(self, sub: UUID, org: UUID, role: str) -> str:
        """Issue a short-lived access token with tenancy claims."""
        return self._encode(
            {"sub": str(sub), "org": str(org), "role": role, "type": "access"},
            ttl_seconds=self._config.access_ttl_seconds,
        )

    def create_refresh_token(self, sub: UUID) -> str:
        """Issue a long-lived refresh token carrying only the subject."""
        return self._encode(
            {"sub": str(sub), "type": "refresh"},
            ttl_seconds=self._config.refresh_ttl_seconds,
        )

    def decode(self, token: str, expected_type: TokenType) -> TokenPayload:
        """Decode and validate a JWT, enforcing signature, expiry, and type.

        Raises :class:`UnauthorizedError` on any failure — expired, invalid
        signature, malformed claims, or a token of the wrong type (a refresh
        token can never be used where an access token is expected).
        """
        try:
            claims = jwt.decode(
                token,
                self._config.jwt_secret,
                algorithms=[self._config.jwt_algorithm],
                options={"require": ["exp", "iat", "sub", "jti"]},
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid or expired token") from exc

        try:
            payload = TokenPayload.model_validate(claims)
        except ValueError as exc:
            raise UnauthorizedError("Malformed token claims") from exc

        if payload.type != expected_type:
            raise UnauthorizedError(f"Expected a {expected_type} token")
        return payload

    def _encode(self, claims: dict[str, Any], *, ttl_seconds: int) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            **claims,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(payload, self._config.jwt_secret, algorithm=self._config.jwt_algorithm)

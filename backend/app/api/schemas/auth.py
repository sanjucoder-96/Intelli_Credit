"""Authentication request/response schemas.

Mirrors :class:`app.application.use_cases.auth.TokenPair` on the wire.
Phase 4 adds logout/rotation payloads alongside the Redis denylist.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Credential login payload."""

    email: EmailStr
    password: str = Field(min_length=8)


class RefreshRequest(BaseModel):
    """Refresh-token exchange payload."""

    refresh_token: str


class TokenPairResponse(BaseModel):
    """Issued token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

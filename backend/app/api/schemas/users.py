"""User-facing response schemas.

Phase 4 adds admin user-management payloads (invite, role change).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import Role


class UserResponse(BaseModel):
    """Public profile of a platform user (never carries the password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    role: Role
    org_id: UUID
    created_at: datetime

"""Identity models: Organization (tenancy root) and User.

Organization is not in the product owner's Phase-2 model list but is the
structural tenancy root required by ARCHITECTURE §14 / ADR-0013 — every
tenant-scoped table carries an ``org_id`` FK to it. Phase 3 adds Postgres
row-level security policies keyed on the same column.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import Role
from app.infrastructure.db.base import (
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant: bank, NBFC, or evaluation sandbox."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)


class User(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A platform user within one organization (ADR-0013)."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    hashed_password: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    role: Mapped[Role] = mapped_column(string_enum(Role), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None)

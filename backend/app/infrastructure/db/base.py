"""Declarative base, naming conventions, and shared ORM mixins.

``Base.metadata`` is the single source of truth consumed by the Alembic env.
Enum columns across all models use ``native_enum=False`` (VARCHAR + CHECK)
so adding members never requires an ALTER TYPE migration. Phase 3 adds a
pgvector-backed chunks table using the same base.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with deterministic constraint naming for Alembic."""

    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def string_enum(enum_cls: type[enum.Enum], **kwargs: Any) -> sa.Enum:
    """Build a VARCHAR-backed enum column type storing member *values*.

    ``native_enum=False`` keeps enum evolution a code deploy instead of a
    database migration; ``length=32`` bounds the column.
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        **kwargs,
    )


class UUIDPrimaryKeyMixin:
    """UUIDv4 primary key generated application-side."""

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)


class TimestampMixin:
    """Server-side created/updated timestamps (timezone-aware UTC)."""

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Soft deletion; repositories exclude rows with ``deleted_at`` set."""

    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None)


class TenantMixin:
    """Mandatory organization scoping column (ADR-0013)."""

    @declared_attr
    def org_id(cls) -> Mapped[UUID]:  # noqa: N805
        return mapped_column(
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )

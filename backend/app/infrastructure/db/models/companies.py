"""Borrower company master data.

Identifier columns (CIN/GSTIN/PAN) store normalized values validated by
``app.domain.value_objects`` at the application boundary — the DB keeps
plain strings. Phase 3 links documents/chunks; Phase 6 links features.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import DataOrigin
from app.infrastructure.db.base import (
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class Company(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A borrower company under credit evaluation."""

    __tablename__ = "companies"
    __table_args__ = (sa.Index("ix_companies_org_id_legal_name", "org_id", "legal_name"),)

    legal_name: Mapped[str] = mapped_column(sa.String(512), nullable=False, index=True)
    cin: Mapped[str | None] = mapped_column(sa.String(21), default=None)
    gstin: Mapped[str | None] = mapped_column(sa.String(15), default=None)
    pan: Mapped[str | None] = mapped_column(sa.String(10), default=None)
    sector: Mapped[str | None] = mapped_column(sa.String(128), default=None)
    registered_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    data_origin: Mapped[DataOrigin] = mapped_column(
        string_enum(DataOrigin), nullable=False, default=DataOrigin.REAL
    )

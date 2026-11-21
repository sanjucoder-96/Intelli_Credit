"""Generated report metadata (intermediate representation + artifacts).

Schema only in Phase 2 — the report IR and renderers land in Phase 5
(ADR-0011). ``ir`` holds the renderer-agnostic intermediate representation;
``artifacts`` maps output format → storage key.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import ReportStatus
from app.infrastructure.db.base import (
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class Report(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """One generated report for a run or application (ADR-0011)."""

    __tablename__ = "reports"

    workflow_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflow_executions.id", ondelete="SET NULL"),
        default=None,
    )
    loan_application_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("loan_applications.id", ondelete="SET NULL"),
        default=None,
    )
    report_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    template_key: Mapped[str | None] = mapped_column(sa.String(128), default=None)
    ir: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    artifacts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    status: Mapped[ReportStatus] = mapped_column(
        string_enum(ReportStatus), nullable=False, default=ReportStatus.PENDING
    )

"""Loan applications — the ARCHITECTURE 'Case' aggregate for the credit
workflow.

Named ``LoanApplication`` per the product owner's Phase-2 model list; the
concept is the ``Case`` of ARCHITECTURE §3/§16. Phase 4 wires status
transitions to ingestion; Phase 5 to workflow executions.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import LoanApplicationStatus
from app.infrastructure.db.base import (
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class LoanApplication(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """One credit-appraisal case for a company."""

    __tablename__ = "loan_applications"
    __table_args__ = (sa.Index("ix_loan_applications_org_id_status", "org_id", "status"),)

    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_key: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    title: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    status: Mapped[LoanApplicationStatus] = mapped_column(
        string_enum(LoanApplicationStatus),
        nullable=False,
        default=LoanApplicationStatus.DRAFT,
    )
    requested_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 2), default=None)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="INR")
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

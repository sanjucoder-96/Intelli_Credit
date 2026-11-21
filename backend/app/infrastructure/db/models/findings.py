"""Finding — append-only analytical claims with supersession links.

Findings are superseded, never mutated (ARCHITECTURE §3): a correction
inserts a new row and points the old one at it, which is what keeps
month-later explainability honest after human corrections (§15.4 correction
cascade). Every finding carries the six-category risk taxonomy tag
(ARCHITECTURE §9) so reports can print risks by required category.
Phase 5 agents write these rows.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import FindingKind, RiskCategory, Severity
from app.infrastructure.db.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class Finding(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One analytical claim produced by an agent, validator, or officer."""

    __tablename__ = "findings"
    __table_args__ = (
        sa.Index("ix_findings_org_id_loan_application_id", "org_id", "loan_application_id"),
        sa.Index("ix_findings_workflow_execution_id", "workflow_execution_id"),
        # The live view: current (non-superseded) findings per case.
        sa.Index(
            "ix_findings_current",
            "loan_application_id",
            postgresql_where=sa.text("superseded_by_id IS NULL"),
        ),
    )

    loan_application_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("loan_applications.id", ondelete="SET NULL"),
        default=None,
    )
    workflow_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflow_executions.id", ondelete="RESTRICT"),
        default=None,
    )
    agent: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    kind: Mapped[FindingKind] = mapped_column(string_enum(FindingKind), nullable=False)
    category: Mapped[RiskCategory | None] = mapped_column(string_enum(RiskCategory), default=None)
    severity: Mapped[Severity] = mapped_column(
        string_enum(Severity), nullable=False, default=Severity.INFO
    )
    title: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    narrative: Mapped[str | None] = mapped_column(sa.Text, default=None)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    confidence: Mapped[float | None] = mapped_column(sa.Float, default=None)
    source_tier: Mapped[float | None] = mapped_column(sa.Float, default=None)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("findings.id", ondelete="RESTRICT"), default=None
    )

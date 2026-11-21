"""Risk assessment results from the deterministic scorecard engine.

Schema only in Phase 2 (ADR-0009) — populated in Phase 6 when the scorecard
and policy-grid engine land. Column shape mirrors the explainability payload
the frontend renders (base score, overlays, breakdown, grade, decision).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import Decision
from app.infrastructure.db.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class RiskAssessment(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One scorecard evaluation for a workflow execution (ADR-0009)."""

    __tablename__ = "risk_assessments"

    workflow_execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflow_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    loan_application_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("loan_applications.id", ondelete="SET NULL"),
        default=None,
    )
    scorecard_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    scorecard_version: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    base_score: Mapped[float | None] = mapped_column(sa.Float, default=None)
    final_score: Mapped[float | None] = mapped_column(sa.Float, default=None)
    grade: Mapped[str | None] = mapped_column(sa.String(8), default=None)
    decision: Mapped[Decision | None] = mapped_column(string_enum(Decision), default=None)
    coverage: Mapped[float | None] = mapped_column(sa.Float, default=None)
    breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    overlay_log: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)


class ScorecardConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable versioned scorecard configuration (ADR-0009).

    Authored as YAML, validated, then persisted here exactly like workflow
    definitions: re-registering a (key, version) with different content is an
    error — every historical assessment stays explainable against the exact
    scorecard version that produced it. Global (no ``org_id``); per-tenant
    scorecard packs are a Phase-6+ concern.
    """

    __tablename__ = "scorecard_configs"
    __table_args__ = (sa.UniqueConstraint("key", "version"),)

    key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checksum: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)


class ScoreBreakdown(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One factor's contribution inside a risk assessment (ADR-0009).

    Every term of ``S = sum_p W_p * sum_f w_f * n_f(x_f)`` is persisted so the
    CAM can print the exact arithmetic and the UI can replay it factor by
    factor. Populated in Phase 6.
    """

    __tablename__ = "score_breakdowns"
    __table_args__ = (sa.Index("ix_score_breakdowns_risk_assessment_id", "risk_assessment_id"),)

    risk_assessment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("risk_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    pillar_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    factor_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    raw_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    normalized: Mapped[float | None] = mapped_column(sa.Float, default=None)
    weight: Mapped[float] = mapped_column(sa.Float, nullable=False)
    contribution: Mapped[float | None] = mapped_column(sa.Float, default=None)
    admitted: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    exclusion_reason: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    evidence_refs: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    reason_text: Mapped[str | None] = mapped_column(sa.Text, default=None)

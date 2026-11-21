"""Workflow definitions (immutable, versioned) and their executions.

``Workflow`` rows are immutable numbered versions of the YAML DSL (ADR-0008):
a run is pinned to the exact definition it executed forever.
``WorkflowExecution`` is the ARCHITECTURE 'WorkflowRun'. Phase 5 wires the
WorkflowRegistry to persist definitions here and the execution engine to
manage rows via the LangGraph Postgres checkpointer.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import WorkflowExecutionStatus
from app.infrastructure.db.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class Workflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable version of a workflow definition — global, not
    tenant-scoped (ADR-0008)."""

    __tablename__ = "workflows"
    __table_args__ = (sa.UniqueConstraint("key", "version"),)

    key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checksum: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)


class WorkflowExecution(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One run of a workflow version (the ARCHITECTURE 'WorkflowRun')."""

    __tablename__ = "workflow_executions"
    __table_args__ = (sa.Index("ix_workflow_executions_org_id_status", "org_id", "status"),)

    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False
    )
    loan_application_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("loan_applications.id", ondelete="SET NULL"),
        default=None,
    )
    status: Mapped[WorkflowExecutionStatus] = mapped_column(
        string_enum(WorkflowExecutionStatus),
        nullable=False,
        default=WorkflowExecutionStatus.PENDING,
    )
    thread_id: Mapped[str | None] = mapped_column(sa.String(64), unique=True, default=None)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None)
    cost_usd: Mapped[Decimal] = mapped_column(sa.Numeric(12, 4), nullable=False, default=Decimal(0))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

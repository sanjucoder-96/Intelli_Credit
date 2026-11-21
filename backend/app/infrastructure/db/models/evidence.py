"""Evidence ledger rows and web-research snapshots.

``ResearchSnapshot`` implements web-evidence permanence (ARCHITECTURE §6):
every cited web source is stored by content hash so a citation remains
inspectable after the live page changes. Phase 5 populates both tables from
the research agents.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import EvidenceKind
from app.infrastructure.db.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class Evidence(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One claim → source link in the evidence ledger (ARCHITECTURE §8)."""

    __tablename__ = "evidence"
    __table_args__ = (sa.Index("ix_evidence_workflow_execution_id", "workflow_execution_id"),)

    loan_application_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("loan_applications.id", ondelete="SET NULL"),
        default=None,
    )
    workflow_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflow_executions.id", ondelete="SET NULL"),
        default=None,
    )
    agent_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
        default=None,
    )
    kind: Mapped[EvidenceKind] = mapped_column(string_enum(EvidenceKind), nullable=False)
    claim: Mapped[str] = mapped_column(sa.Text, nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), default=None
    )
    page: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    bboxes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    url: Mapped[str | None] = mapped_column(sa.Text, default=None)
    snapshot_key: Mapped[str | None] = mapped_column(sa.String(512), default=None)
    quote: Mapped[str | None] = mapped_column(sa.Text, default=None)
    source_tier: Mapped[float | None] = mapped_column(sa.Float, default=None)
    confidence: Mapped[float | None] = mapped_column(sa.Float, default=None)
    created_by_agent: Mapped[str | None] = mapped_column(sa.String(64), default=None)


class ResearchSnapshot(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Content-addressed snapshot of a fetched web source (ARCHITECTURE §6)."""

    __tablename__ = "research_snapshots"

    workflow_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflow_executions.id", ondelete="SET NULL"),
        default=None,
    )
    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.Text, default=None)
    source_tier: Mapped[float | None] = mapped_column(sa.Float, default=None)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)

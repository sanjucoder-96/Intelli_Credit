"""Document storage metadata, ingestion checkpoints, and extracted fields.

``ExtractedField`` rows are superseded, never mutated (ARCHITECTURE §3):
a correction inserts a new row and points the old one at it via
``superseded_by_id`` — month-later explainability stays honest.
``DocumentStage`` checkpoints make Celery's at-least-once delivery safe:
every ingestion task first reads its checkpoint and no-ops if DONE
(ARCHITECTURE §15.3). Phase 4 populates these tables from the pipeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import DataOrigin, DocumentStatus, IngestStage, StageStatus
from app.infrastructure.db.base import (
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class Document(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """An uploaded file (or a child extracted from an archive via parent_id)."""

    __tablename__ = "documents"
    __table_args__ = (
        sa.Index("ix_documents_org_id_loan_application_id", "org_id", "loan_application_id"),
    )

    loan_application_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("loan_applications.id", ondelete="SET NULL"),
        default=None,
    )
    company_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="SET NULL"), default=None
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), default=None
    )
    storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    doc_type: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="unclassified")
    doc_type_confidence: Mapped[float | None] = mapped_column(sa.Float, default=None)
    status: Mapped[DocumentStatus] = mapped_column(
        string_enum(DocumentStatus), nullable=False, default=DocumentStatus.UPLOADED
    )
    page_count: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    data_origin: Mapped[DataOrigin] = mapped_column(
        string_enum(DataOrigin), nullable=False, default=DataOrigin.REAL
    )


class ExtractedField(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One extracted field value — append-only with supersession links."""

    __tablename__ = "extracted_fields"
    __table_args__ = (
        sa.Index("ix_extracted_fields_document_id_field_path", "document_id", "field_path"),
    )

    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    schema_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    field_path: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    confidence: Mapped[float | None] = mapped_column(sa.Float, default=None)
    page: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    is_superseded: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("extracted_fields.id", ondelete="SET NULL"),
        default=None,
    )


class DocumentStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Checkpoint of one (document, ingestion-stage) pair.

    Deterministic task IDs (``{doc_id}:{stage}``) plus this row make retried
    Celery deliveries idempotent — the stage no-ops when already DONE. The
    table doubles as the live pipeline-progress feed for the UI (ADR-0004).
    Tenant scope is inherited through the document (no separate ``org_id``).
    """

    __tablename__ = "document_stages"
    __table_args__ = (sa.UniqueConstraint("document_id", "stage"),)

    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[IngestStage] = mapped_column(string_enum(IngestStage), nullable=False)
    status: Mapped[StageStatus] = mapped_column(
        string_enum(StageStatus), nullable=False, default=StageStatus.PENDING
    )
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None)

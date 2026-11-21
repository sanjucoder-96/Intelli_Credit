"""Chunk — the hybrid-retrieval unit (ADR-0002, ARCHITECTURE §8).

Text, metadata, the ``tsvector`` (generated column, always in sync), and the
embedding vector live in ONE row, so indexing is transactional with
extraction. The embedding dimension is 384 (fastembed ``bge-small-en-v1.5``,
the ADR-0007 default); swapping providers requires an Alembic dim migration
plus a checkpointed re-embed job — recorded in ADR-0007. Phase 4 populates
this table from the ingestion pipeline.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIM = 384
"""bge-small-en-v1.5 output dimension (ADR-0007)."""


class Chunk(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One retrievable chunk of a document."""

    __tablename__ = "chunks"
    __table_args__ = (
        sa.Index("ix_chunks_org_id_loan_application_id", "org_id", "loan_application_id"),
        # HNSW (cosine) for the vector arm; requires pgvector >= 0.8 with
        # iterative index scans so selective case filters don't starve recall.
        sa.Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        # GIN for the FTS arm.
        sa.Index("ix_chunks_tsv_gin", "tsv", postgresql_using="gin"),
    )

    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    loan_application_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("loan_applications.id", ondelete="SET NULL"),
        default=None,
    )
    doc_type: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="unclassified")
    chunk_kind: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="prose")
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    section_path: Mapped[list[str] | None] = mapped_column(JSONB, default=None)
    page_start: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    page_end: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    bboxes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    fiscal_period: Mapped[str | None] = mapped_column(sa.String(16), default=None)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        sa.Computed("to_tsvector('english', text)", persisted=True),
        nullable=False,
    )

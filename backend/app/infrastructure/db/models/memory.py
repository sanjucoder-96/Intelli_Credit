"""MemoryItem — distilled episodic memory per company (ARCHITECTURE §5).

Written by the Distiller tail node (Phase 5); recalled via the same hybrid
retrieval mechanics as chunks (pgvector + FTS). Powers longitudinal insight
("DSCR deteriorated vs the March run") in the monitoring workflows.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.db.models.chunks import EMBEDDING_DIM


class MemoryItem(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One distilled fact about a company, recallable across runs."""

    __tablename__ = "memory_items"
    __table_args__ = (
        sa.Index("ix_memory_items_org_id_company_id", "org_id", "company_id"),
        sa.Index(
            "ix_memory_items_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        sa.Index("ix_memory_items_tsv_gin", "tsv", postgresql_using="gin"),
    )

    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    fact: Mapped[str] = mapped_column(sa.Text, nullable=False)
    category: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    valid_from: Mapped[date | None] = mapped_column(sa.Date, default=None)
    source_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflow_executions.id", ondelete="SET NULL"),
        default=None,
    )
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        sa.Computed("to_tsvector('english', fact)", persisted=True),
        nullable=False,
    )

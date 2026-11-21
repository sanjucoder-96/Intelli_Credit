"""Platform tables: audit log, feature flags, and the ML model registry.

``AuditLog`` records every mutation for the tenancy/audit story (ADR-0013);
Phase 4 wires writes from the API layer. ``FeatureFlag`` backs the typed
accessor of ADR-0015 (global / per-org / per-workflow scopes). ``ModelRegistry``
(table ``ml_models``) is the Postgres-native ML registry of ADR-0010; Phase 6
training pipelines populate it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import FlagScope, ModelStage
from app.infrastructure.db.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class AuditLog(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One audited action performed by a user (or the system) in an org."""

    __tablename__ = "audit_logs"
    __table_args__ = (sa.Index("ix_audit_logs_org_id_created_at", "org_id", "created_at"),)

    actor_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    action: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    entity_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    ip: Mapped[str | None] = mapped_column(sa.String(64), default=None)


class FeatureFlag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A feature flag at global, per-org, or per-workflow scope (ADR-0015).

    ``org_id`` is nullable directly (not via TenantMixin) because GLOBAL and
    WORKFLOW-scoped flags have no tenant. The unique constraint uses
    ``NULLS NOT DISTINCT`` (PostgreSQL 15+) so at most one row exists per
    (key, scope, org, workflow) combination even when scope columns are NULL.
    """

    __tablename__ = "feature_flags"
    __table_args__ = (
        sa.UniqueConstraint(
            "key",
            "scope",
            "org_id",
            "workflow_key",
            postgresql_nulls_not_distinct=True,
        ),
    )

    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    scope: Mapped[FlagScope] = mapped_column(
        string_enum(FlagScope), nullable=False, default=FlagScope.GLOBAL
    )
    org_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        default=None,
        index=True,
    )
    workflow_key: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)


class ModelRegistry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One versioned ML model artifact in the registry (ADR-0010).

    Table name ``ml_models``. ``data_lineage`` carries dataset hashes so CI
    can verify no SYNTHETIC_DEMO data ever reached training (ADR-0010).
    """

    __tablename__ = "ml_models"
    __table_args__ = (sa.UniqueConstraint("name", "version"),)

    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    stage: Mapped[ModelStage] = mapped_column(
        string_enum(ModelStage), nullable=False, default=ModelStage.CANDIDATE
    )
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    feature_list: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    artifact_path: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    data_lineage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    git_sha: Mapped[str | None] = mapped_column(sa.String(40), default=None)

"""Initial schema — all 17 Phase-2 tables plus the pgvector extension.

Revision ID: 0001
Revises:
Create Date: 2026-07-16

Hand-written (not an autogen artifact) so every constraint and index name is
explicit and matches the metadata naming convention in
``app.infrastructure.db.base``. The pgvector extension is created first —
supported from day one per ADR-0002 — although vector *columns* arrive with
the chunks table in Phase 3. Enum columns are plain VARCHAR(32) (SQLAlchemy
``native_enum=False``, no CHECK constraint) so adding members is a code
deploy, not a migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = sa.Enum("analyst", "credit_officer", "admin", name="role", native_enum=False, length=32)
_DOCUMENT_STATUS = sa.Enum(
    "uploaded",
    "processing",
    "needs_password",
    "ready",
    "failed",
    name="documentstatus",
    native_enum=False,
    length=32,
)
_LOAN_APPLICATION_STATUS = sa.Enum(
    "draft",
    "ingesting",
    "running",
    "needs_review",
    "decided",
    "archived",
    name="loanapplicationstatus",
    native_enum=False,
    length=32,
)
_WORKFLOW_EXECUTION_STATUS = sa.Enum(
    "pending",
    "running",
    "paused_hitl",
    "needs_attention",
    "completed",
    "failed",
    "cancelled",
    name="workflowexecutionstatus",
    native_enum=False,
    length=32,
)
_AGENT_EXECUTION_KIND = sa.Enum(
    "plan",
    "llm_call",
    "tool_call",
    "reflect",
    "revise",
    "final",
    "error",
    name="agentexecutionkind",
    native_enum=False,
    length=32,
)
_EXECUTION_STATUS = sa.Enum(
    "ok", "degraded", "error", name="executionstatus", native_enum=False, length=32
)
_EVIDENCE_KIND = sa.Enum(
    "document", "web", "primary", "derived", name="evidencekind", native_enum=False, length=32
)
_DECISION = sa.Enum("approve", "refer", "decline", name="decision", native_enum=False, length=32)
_DATA_ORIGIN = sa.Enum("real", "synthetic_demo", name="dataorigin", native_enum=False, length=32)
_MODEL_STAGE = sa.Enum(
    "candidate", "champion", "archived", name="modelstage", native_enum=False, length=32
)
_FLAG_SCOPE = sa.Enum("global", "org", "workflow", name="flagscope", native_enum=False, length=32)
_REPORT_STATUS = sa.Enum(
    "pending", "rendered", "failed", name="reportstatus", native_enum=False, length=32
)


def _uuid_pk() -> sa.Column[Any]:
    return sa.Column("id", pg.UUID(as_uuid=True), nullable=False)


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _org_fk(table: str, *, nullable: bool = False, ondelete: str = "RESTRICT") -> sa.Column[Any]:
    return sa.Column(
        "org_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey(
            "organizations.id", ondelete=ondelete, name=f"fk_{table}_org_id_organizations"
        ),
        nullable=nullable,
    )


def upgrade() -> None:
    # pgvector is available from day one (ADR-0002); vector columns land in
    # Phase 3 with the chunks table.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "organizations",
        _uuid_pk(),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )

    op.create_table(
        "users",
        _uuid_pk(),
        _org_fk("users"),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(256), nullable=False),
        sa.Column("hashed_password", sa.String(512), nullable=False),
        sa.Column("role", _ROLE, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"])

    op.create_table(
        "companies",
        _uuid_pk(),
        _org_fk("companies"),
        sa.Column("legal_name", sa.String(512), nullable=False),
        sa.Column("cin", sa.String(21), nullable=True),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("pan", sa.String(10), nullable=True),
        sa.Column("sector", sa.String(128), nullable=True),
        sa.Column("registered_address", pg.JSONB(), nullable=True),
        sa.Column("extra", pg.JSONB(), nullable=True),
        sa.Column("data_origin", _DATA_ORIGIN, nullable=False),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_companies"),
    )
    op.create_index("ix_companies_org_id", "companies", ["org_id"])
    op.create_index("ix_companies_legal_name", "companies", ["legal_name"])
    op.create_index("ix_companies_org_id_legal_name", "companies", ["org_id", "legal_name"])

    op.create_table(
        "loan_applications",
        _uuid_pk(),
        _org_fk("loan_applications"),
        sa.Column(
            "company_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "companies.id",
                ondelete="RESTRICT",
                name="fk_loan_applications_company_id_companies",
            ),
            nullable=False,
        ),
        sa.Column("workflow_key", sa.String(64), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("status", _LOAN_APPLICATION_STATUS, nullable=False),
        sa.Column("requested_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "created_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_loan_applications_created_by_users"
            ),
            nullable=True,
        ),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_loan_applications"),
    )
    op.create_index("ix_loan_applications_org_id", "loan_applications", ["org_id"])
    op.create_index("ix_loan_applications_org_id_status", "loan_applications", ["org_id", "status"])

    op.create_table(
        "workflows",
        _uuid_pk(),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", pg.JSONB(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_workflows"),
        sa.UniqueConstraint("key", "version", name="uq_workflows_key"),
    )

    op.create_table(
        "workflow_executions",
        _uuid_pk(),
        _org_fk("workflow_executions"),
        sa.Column(
            "workflow_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflows.id",
                ondelete="RESTRICT",
                name="fk_workflow_executions_workflow_id_workflows",
            ),
            nullable=False,
        ),
        sa.Column(
            "loan_application_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "loan_applications.id",
                ondelete="SET NULL",
                name="fk_workflow_executions_loan_application_id_loan_applications",
            ),
            nullable=True,
        ),
        sa.Column("status", _WORKFLOW_EXECUTION_STATUS, nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("error", pg.JSONB(), nullable=True),
        sa.Column(
            "created_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_workflow_executions_created_by_users"
            ),
            nullable=True,
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_executions"),
        sa.UniqueConstraint("thread_id", name="uq_workflow_executions_thread_id"),
    )
    op.create_index("ix_workflow_executions_org_id", "workflow_executions", ["org_id"])
    op.create_index(
        "ix_workflow_executions_org_id_status", "workflow_executions", ["org_id", "status"]
    )

    op.create_table(
        "documents",
        _uuid_pk(),
        _org_fk("documents"),
        sa.Column(
            "loan_application_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "loan_applications.id",
                ondelete="SET NULL",
                name="fk_documents_loan_application_id_loan_applications",
            ),
            nullable=True,
        ),
        sa.Column(
            "company_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "companies.id", ondelete="SET NULL", name="fk_documents_company_id_companies"
            ),
            nullable=True,
        ),
        sa.Column(
            "parent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "documents.id", ondelete="SET NULL", name="fk_documents_parent_id_documents"
            ),
            nullable=True,
        ),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("doc_type", sa.String(64), nullable=False),
        sa.Column("doc_type_confidence", sa.Float(), nullable=True),
        sa.Column("status", _DOCUMENT_STATUS, nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("data_origin", _DATA_ORIGIN, nullable=False),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_index("ix_documents_org_id", "documents", ["org_id"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])
    op.create_index(
        "ix_documents_org_id_loan_application_id", "documents", ["org_id", "loan_application_id"]
    )

    op.create_table(
        "agent_executions",
        _uuid_pk(),
        _org_fk("agent_executions"),
        sa.Column(
            "workflow_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflow_executions.id",
                ondelete="CASCADE",
                name="fk_agent_executions_workflow_execution_id_workflow_executions",
            ),
            nullable=False,
        ),
        sa.Column("node", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("kind", _AGENT_EXECUTION_KIND, nullable=False),
        sa.Column("status", _EXECUTION_STATUS, nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("output", pg.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confidence_components", pg.JSONB(), nullable=True),
        sa.Column("citations", pg.JSONB(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_agent_executions"),
    )
    op.create_index("ix_agent_executions_org_id", "agent_executions", ["org_id"])
    op.create_index(
        "ix_agent_executions_workflow_execution_id_seq",
        "agent_executions",
        ["workflow_execution_id", "seq"],
    )

    op.create_table(
        "extracted_fields",
        _uuid_pk(),
        _org_fk("extracted_fields"),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "documents.id", ondelete="CASCADE", name="fk_extracted_fields_document_id_documents"
            ),
            nullable=False,
        ),
        sa.Column("schema_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("field_path", sa.String(256), nullable=False),
        sa.Column("value", pg.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("bbox", pg.JSONB(), nullable=True),
        sa.Column("is_superseded", sa.Boolean(), nullable=False),
        sa.Column(
            "superseded_by_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "extracted_fields.id",
                ondelete="SET NULL",
                name="fk_extracted_fields_superseded_by_id_extracted_fields",
            ),
            nullable=True,
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_extracted_fields"),
    )
    op.create_index("ix_extracted_fields_org_id", "extracted_fields", ["org_id"])
    op.create_index(
        "ix_extracted_fields_document_id_field_path",
        "extracted_fields",
        ["document_id", "field_path"],
    )

    op.create_table(
        "evidence",
        _uuid_pk(),
        _org_fk("evidence"),
        sa.Column(
            "loan_application_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "loan_applications.id",
                ondelete="SET NULL",
                name="fk_evidence_loan_application_id_loan_applications",
            ),
            nullable=True,
        ),
        sa.Column(
            "workflow_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflow_executions.id",
                ondelete="SET NULL",
                name="fk_evidence_workflow_execution_id_workflow_executions",
            ),
            nullable=True,
        ),
        sa.Column(
            "agent_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "agent_executions.id",
                ondelete="SET NULL",
                name="fk_evidence_agent_execution_id_agent_executions",
            ),
            nullable=True,
        ),
        sa.Column("kind", _EVIDENCE_KIND, nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "documents.id", ondelete="SET NULL", name="fk_evidence_document_id_documents"
            ),
            nullable=True,
        ),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("bboxes", pg.JSONB(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("snapshot_key", sa.String(512), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("source_tier", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_by_agent", sa.String(64), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_evidence"),
    )
    op.create_index("ix_evidence_org_id", "evidence", ["org_id"])
    op.create_index("ix_evidence_workflow_execution_id", "evidence", ["workflow_execution_id"])

    op.create_table(
        "research_snapshots",
        _uuid_pk(),
        _org_fk("research_snapshots"),
        sa.Column(
            "workflow_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflow_executions.id",
                ondelete="SET NULL",
                name="fk_research_snapshots_workflow_execution_id_workflow_executions",
            ),
            nullable=True,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_tier", sa.Float(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", pg.JSONB(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_research_snapshots"),
    )
    op.create_index("ix_research_snapshots_org_id", "research_snapshots", ["org_id"])
    op.create_index(
        "ix_research_snapshots_content_sha256", "research_snapshots", ["content_sha256"]
    )

    op.create_table(
        "llm_interactions",
        _uuid_pk(),
        _org_fk("llm_interactions", nullable=True),
        sa.Column(
            "workflow_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflow_executions.id",
                ondelete="SET NULL",
                name="fk_llm_interactions_workflow_execution_id_workflow_executions",
            ),
            nullable=True,
        ),
        sa.Column(
            "agent_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "agent_executions.id",
                ondelete="SET NULL",
                name="fk_llm_interactions_agent_execution_id_agent_executions",
            ),
            nullable=True,
        ),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("status", _EXECUTION_STATUS, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload_ref", sa.String(512), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_llm_interactions"),
    )
    op.create_index("ix_llm_interactions_org_id", "llm_interactions", ["org_id"])
    op.create_index("ix_llm_interactions_prompt_sha256", "llm_interactions", ["prompt_sha256"])
    op.create_index(
        "ix_llm_interactions_model_created_at", "llm_interactions", ["model", "created_at"]
    )

    op.create_table(
        "risk_assessments",
        _uuid_pk(),
        _org_fk("risk_assessments"),
        sa.Column(
            "workflow_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflow_executions.id",
                ondelete="CASCADE",
                name="fk_risk_assessments_workflow_execution_id_workflow_executions",
            ),
            nullable=False,
        ),
        sa.Column(
            "loan_application_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "loan_applications.id",
                ondelete="SET NULL",
                name="fk_risk_assessments_loan_application_id_loan_applications",
            ),
            nullable=True,
        ),
        sa.Column("scorecard_key", sa.String(64), nullable=False),
        sa.Column("scorecard_version", sa.String(16), nullable=False),
        sa.Column("base_score", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("grade", sa.String(8), nullable=True),
        sa.Column("decision", _DECISION, nullable=True),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("breakdown", pg.JSONB(), nullable=True),
        sa.Column("overlay_log", pg.JSONB(), nullable=True),
        sa.Column("explanation", pg.JSONB(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_risk_assessments"),
    )
    op.create_index("ix_risk_assessments_org_id", "risk_assessments", ["org_id"])

    op.create_table(
        "reports",
        _uuid_pk(),
        _org_fk("reports"),
        sa.Column(
            "workflow_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflow_executions.id",
                ondelete="SET NULL",
                name="fk_reports_workflow_execution_id_workflow_executions",
            ),
            nullable=True,
        ),
        sa.Column(
            "loan_application_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "loan_applications.id",
                ondelete="SET NULL",
                name="fk_reports_loan_application_id_loan_applications",
            ),
            nullable=True,
        ),
        sa.Column("report_type", sa.String(64), nullable=False),
        sa.Column("template_key", sa.String(128), nullable=True),
        sa.Column("ir", pg.JSONB(), nullable=True),
        sa.Column("artifacts", pg.JSONB(), nullable=True),
        sa.Column("status", _REPORT_STATUS, nullable=False),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reports"),
    )
    op.create_index("ix_reports_org_id", "reports", ["org_id"])

    op.create_table(
        "audit_logs",
        _uuid_pk(),
        _org_fk("audit_logs"),
        sa.Column(
            "actor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_audit_logs_actor_id_users"),
            nullable=True,
        ),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("entity_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", pg.JSONB(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])
    op.create_index("ix_audit_logs_org_id_created_at", "audit_logs", ["org_id", "created_at"])

    op.create_table(
        "feature_flags",
        _uuid_pk(),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("scope", _FLAG_SCOPE, nullable=False),
        _org_fk("feature_flags", nullable=True, ondelete="CASCADE"),
        sa.Column("workflow_key", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("value", pg.JSONB(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_feature_flags"),
        sa.UniqueConstraint(
            "key",
            "scope",
            "org_id",
            "workflow_key",
            name="uq_feature_flags_key",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_feature_flags_org_id", "feature_flags", ["org_id"])

    op.create_table(
        "ml_models",
        _uuid_pk(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("stage", _MODEL_STAGE, nullable=False),
        sa.Column("metrics", pg.JSONB(), nullable=True),
        sa.Column("feature_list", pg.JSONB(), nullable=True),
        sa.Column("artifact_path", sa.String(512), nullable=False),
        sa.Column("data_lineage", pg.JSONB(), nullable=True),
        sa.Column("git_sha", sa.String(40), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_ml_models"),
        sa.UniqueConstraint("name", "version", name="uq_ml_models_name"),
    )


def downgrade() -> None:
    # Drop in FK-safe reverse dependency order. The pgvector extension is
    # deliberately left in place: other schemas/databases may share it and
    # dropping an extension is an operator decision, not a migration's.
    op.drop_table("ml_models")
    op.drop_table("feature_flags")
    op.drop_table("audit_logs")
    op.drop_table("reports")
    op.drop_table("risk_assessments")
    op.drop_table("llm_interactions")
    op.drop_table("evidence")
    op.drop_table("research_snapshots")
    op.drop_table("extracted_fields")
    op.drop_table("agent_executions")
    op.drop_table("documents")
    op.drop_table("workflow_executions")
    op.drop_table("workflows")
    op.drop_table("loan_applications")
    op.drop_table("companies")
    op.drop_table("users")
    op.drop_table("organizations")

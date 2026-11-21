"""Phase-3 schema: retrieval + evidence-graph tables, review carry-ins, RLS.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-17

Adds the seven Phase-3 tables (chunks and memory_items with pgvector HNSW +
generated tsvector columns, document_stages checkpoints, append-only findings,
scorecard_configs / score_breakdowns, agent_execution_payloads), applies the
Phase-2 review carry-ins (FK indexes on obvious query paths, feature-flag
scope CHECK constraints, trace-table ondelete posture CASCADE/SET NULL →
RESTRICT), and enables **row-level security** with FORCE on every org-scoped
table (ADR-0013 second isolation layer). Policies key on the transaction GUC
``app.current_org_id`` set by the tenant session; a missing GUC means zero
rows — default deny. RLS bites for the non-superuser application role
(``app.infrastructure.db.rls.ensure_app_role``); the admin/owner engine
(migrations, login path, seeds) is a Postgres superuser in dev and bypasses
policies by design.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 384  # bge-small-en-v1.5 (ADR-0007)

_INGEST_STAGE = sa.Enum(
    "detect_type",
    "extract_text_layer",
    "ocr_pages",
    "layout_tables",
    "classify",
    "extract_domain",
    "chunk",
    "embed_index",
    "cross_validate",
    name="ingeststage",
    native_enum=False,
    length=32,
)
_STAGE_STATUS = sa.Enum(
    "pending",
    "running",
    "done",
    "failed",
    "skipped",
    name="stagestatus",
    native_enum=False,
    length=32,
)
_FINDING_KIND = sa.Enum(
    "extraction",
    "research",
    "fraud",
    "validation",
    "primary",
    "gap",
    "derived",
    name="findingkind",
    native_enum=False,
    length=32,
)
_RISK_CATEGORY = sa.Enum(
    "business",
    "financial",
    "operational",
    "legal",
    "reputational",
    "macro",
    name="riskcategory",
    native_enum=False,
    length=32,
)
_SEVERITY = sa.Enum(
    "info", "low", "medium", "high", "critical", name="severity", native_enum=False, length=32
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


# Tables whose rows belong to exactly one tenant: plain equality policy.
_RLS_STRICT_TABLES = (
    "users",
    "companies",
    "loan_applications",
    "documents",
    "extracted_fields",
    "evidence",
    "research_snapshots",
    "workflow_executions",
    "agent_executions",
    "risk_assessments",
    "reports",
    "audit_logs",
    "chunks",
    "findings",
    "memory_items",
    "score_breakdowns",
)
_ORG_GUC = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def upgrade() -> None:
    # ------------------------------------------------------------- new tables
    op.create_table(
        "chunks",
        _uuid_pk(),
        _org_fk("chunks"),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "documents.id", ondelete="CASCADE", name="fk_chunks_document_id_documents"
            ),
            nullable=False,
        ),
        sa.Column(
            "loan_application_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "loan_applications.id",
                ondelete="SET NULL",
                name="fk_chunks_loan_application_id_loan_applications",
            ),
            nullable=True,
        ),
        sa.Column("doc_type", sa.String(64), nullable=False),
        sa.Column("chunk_kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section_path", pg.JSONB(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("bboxes", pg.JSONB(), nullable=True),
        sa.Column("fiscal_period", sa.String(16), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "tsv",
            pg.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
    )
    op.create_index("ix_chunks_org_id", "chunks", ["org_id"])
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index(
        "ix_chunks_org_id_loan_application_id", "chunks", ["org_id", "loan_application_id"]
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"m": 16, "ef_construction": 64},
    )
    op.create_index("ix_chunks_tsv_gin", "chunks", ["tsv"], postgresql_using="gin")

    op.create_table(
        "memory_items",
        _uuid_pk(),
        _org_fk("memory_items"),
        sa.Column(
            "company_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "companies.id", ondelete="CASCADE", name="fk_memory_items_company_id_companies"
            ),
            nullable=False,
        ),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column(
            "source_run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflow_executions.id",
                ondelete="SET NULL",
                name="fk_memory_items_source_run_id_workflow_executions",
            ),
            nullable=True,
        ),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "tsv",
            pg.TSVECTOR(),
            sa.Computed("to_tsvector('english', fact)", persisted=True),
            nullable=False,
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_memory_items"),
    )
    op.create_index("ix_memory_items_org_id", "memory_items", ["org_id"])
    op.create_index("ix_memory_items_org_id_company_id", "memory_items", ["org_id", "company_id"])
    op.create_index(
        "ix_memory_items_embedding_hnsw",
        "memory_items",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"m": 16, "ef_construction": 64},
    )
    op.create_index("ix_memory_items_tsv_gin", "memory_items", ["tsv"], postgresql_using="gin")

    op.create_table(
        "document_stages",
        _uuid_pk(),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "documents.id", ondelete="CASCADE", name="fk_document_stages_document_id_documents"
            ),
            nullable=False,
        ),
        sa.Column("stage", _INGEST_STAGE, nullable=False),
        sa.Column("status", _STAGE_STATUS, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error", pg.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_document_stages"),
        sa.UniqueConstraint("document_id", "stage", name="uq_document_stages_document_id"),
    )
    op.create_index("ix_document_stages_document_id", "document_stages", ["document_id"])

    op.create_table(
        "findings",
        _uuid_pk(),
        _org_fk("findings"),
        sa.Column(
            "loan_application_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "loan_applications.id",
                ondelete="SET NULL",
                name="fk_findings_loan_application_id_loan_applications",
            ),
            nullable=True,
        ),
        sa.Column(
            "workflow_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "workflow_executions.id",
                ondelete="RESTRICT",
                name="fk_findings_workflow_execution_id_workflow_executions",
            ),
            nullable=True,
        ),
        sa.Column("agent", sa.String(64), nullable=True),
        sa.Column("kind", _FINDING_KIND, nullable=False),
        sa.Column("category", _RISK_CATEGORY, nullable=True),
        sa.Column("severity", _SEVERITY, nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("data", pg.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_tier", sa.Float(), nullable=True),
        sa.Column(
            "superseded_by_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "findings.id", ondelete="RESTRICT", name="fk_findings_superseded_by_id_findings"
            ),
            nullable=True,
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_findings"),
    )
    op.create_index("ix_findings_org_id", "findings", ["org_id"])
    op.create_index(
        "ix_findings_org_id_loan_application_id", "findings", ["org_id", "loan_application_id"]
    )
    op.create_index("ix_findings_workflow_execution_id", "findings", ["workflow_execution_id"])
    op.create_index(
        "ix_findings_current",
        "findings",
        ["loan_application_id"],
        postgresql_where=sa.text("superseded_by_id IS NULL"),
    )

    op.create_table(
        "scorecard_configs",
        _uuid_pk(),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_scorecard_configs"),
        sa.UniqueConstraint("key", "version", name="uq_scorecard_configs_key"),
    )

    op.create_table(
        "score_breakdowns",
        _uuid_pk(),
        _org_fk("score_breakdowns"),
        sa.Column(
            "risk_assessment_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "risk_assessments.id",
                ondelete="CASCADE",
                name="fk_score_breakdowns_risk_assessment_id_risk_assessments",
            ),
            nullable=False,
        ),
        sa.Column("pillar_id", sa.String(64), nullable=False),
        sa.Column("factor_id", sa.String(128), nullable=False),
        sa.Column("raw_value", pg.JSONB(), nullable=True),
        sa.Column("normalized", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("contribution", sa.Float(), nullable=True),
        sa.Column("admitted", sa.Boolean(), nullable=False),
        sa.Column("exclusion_reason", sa.String(64), nullable=True),
        sa.Column("evidence_refs", pg.JSONB(), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_score_breakdowns"),
    )
    op.create_index("ix_score_breakdowns_org_id", "score_breakdowns", ["org_id"])
    op.create_index(
        "ix_score_breakdowns_risk_assessment_id", "score_breakdowns", ["risk_assessment_id"]
    )

    op.create_table(
        "agent_execution_payloads",
        _uuid_pk(),
        sa.Column(
            "agent_execution_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "agent_executions.id",
                ondelete="RESTRICT",
                name="fk_agent_execution_payloads_agent_execution_id_agent_executions",
            ),
            nullable=False,
        ),
        sa.Column("prompt_gz", sa.LargeBinary(), nullable=True),
        sa.Column("output_gz", sa.LargeBinary(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_agent_execution_payloads"),
        sa.UniqueConstraint(
            "agent_execution_id", name="uq_agent_execution_payloads_agent_execution_id"
        ),
    )

    # -------------------------------------------- review carry-ins: FK indexes
    op.create_index(
        "ix_risk_assessments_workflow_execution_id", "risk_assessments", ["workflow_execution_id"]
    )
    op.create_index(
        "ix_llm_interactions_workflow_execution_id", "llm_interactions", ["workflow_execution_id"]
    )
    op.create_index("ix_reports_workflow_execution_id", "reports", ["workflow_execution_id"])
    op.create_index("ix_loan_applications_company_id", "loan_applications", ["company_id"])
    op.create_index("ix_evidence_loan_application_id", "evidence", ["loan_application_id"])
    op.create_index("ix_evidence_document_id", "evidence", ["document_id"])
    op.create_index("ix_workflow_executions_workflow_id", "workflow_executions", ["workflow_id"])

    # ------------------------------- review carry-ins: flag CHECK constraints
    op.create_check_constraint(
        "ck_feature_flags_scope_global",
        "feature_flags",
        "scope != 'global' OR (org_id IS NULL AND workflow_key IS NULL)",
    )
    op.create_check_constraint(
        "ck_feature_flags_scope_org", "feature_flags", "scope != 'org' OR org_id IS NOT NULL"
    )
    op.create_check_constraint(
        "ck_feature_flags_scope_workflow",
        "feature_flags",
        "scope != 'workflow' OR (workflow_key IS NOT NULL AND org_id IS NULL)",
    )

    # ------------- review carry-ins: audit-posture ondelete (trace tables must
    # block run deletion, not silently cascade or orphan)
    op.drop_constraint(
        "fk_agent_executions_workflow_execution_id_workflow_executions",
        "agent_executions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_agent_executions_workflow_execution_id_workflow_executions",
        "agent_executions",
        "workflow_executions",
        ["workflow_execution_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "fk_evidence_workflow_execution_id_workflow_executions", "evidence", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_evidence_workflow_execution_id_workflow_executions",
        "evidence",
        "workflow_executions",
        ["workflow_execution_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "fk_llm_interactions_workflow_execution_id_workflow_executions",
        "llm_interactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_llm_interactions_workflow_execution_id_workflow_executions",
        "llm_interactions",
        "workflow_executions",
        ["workflow_execution_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ------------------------------------------------- row-level security (RLS)
    for table in _RLS_STRICT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING (org_id = {_ORG_GUC}) WITH CHECK (org_id = {_ORG_GUC})"
        )

    # feature_flags: tenants read global rows (org_id IS NULL) plus their own
    # org rows, but may only write rows stamped with their org.
    op.execute("ALTER TABLE feature_flags ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feature_flags FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY feature_flags_tenant_isolation ON feature_flags "
        f"USING (org_id IS NULL OR org_id = {_ORG_GUC}) WITH CHECK (org_id = {_ORG_GUC})"
    )

    # llm_interactions: org_id is nullable (platform-level calls); tenant
    # sessions see and write only their own rows — NULL rows are admin-only.
    op.execute("ALTER TABLE llm_interactions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE llm_interactions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY llm_interactions_tenant_isolation ON llm_interactions "
        f"USING (org_id = {_ORG_GUC}) WITH CHECK (org_id = {_ORG_GUC})"
    )


def downgrade() -> None:
    for table in (*_RLS_STRICT_TABLES, "feature_flags", "llm_interactions"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_constraint(
        "fk_llm_interactions_workflow_execution_id_workflow_executions",
        "llm_interactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_llm_interactions_workflow_execution_id_workflow_executions",
        "llm_interactions",
        "workflow_executions",
        ["workflow_execution_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "fk_evidence_workflow_execution_id_workflow_executions", "evidence", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_evidence_workflow_execution_id_workflow_executions",
        "evidence",
        "workflow_executions",
        ["workflow_execution_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "fk_agent_executions_workflow_execution_id_workflow_executions",
        "agent_executions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_agent_executions_workflow_execution_id_workflow_executions",
        "agent_executions",
        "workflow_executions",
        ["workflow_execution_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("ck_feature_flags_scope_workflow", "feature_flags", type_="check")
    op.drop_constraint("ck_feature_flags_scope_org", "feature_flags", type_="check")
    op.drop_constraint("ck_feature_flags_scope_global", "feature_flags", type_="check")

    op.drop_index("ix_workflow_executions_workflow_id", table_name="workflow_executions")
    op.drop_index("ix_evidence_document_id", table_name="evidence")
    op.drop_index("ix_evidence_loan_application_id", table_name="evidence")
    op.drop_index("ix_loan_applications_company_id", table_name="loan_applications")
    op.drop_index("ix_reports_workflow_execution_id", table_name="reports")
    op.drop_index("ix_llm_interactions_workflow_execution_id", table_name="llm_interactions")
    op.drop_index("ix_risk_assessments_workflow_execution_id", table_name="risk_assessments")

    op.drop_table("agent_execution_payloads")
    op.drop_table("score_breakdowns")
    op.drop_table("scorecard_configs")
    op.drop_table("findings")
    op.drop_table("document_stages")
    op.drop_table("memory_items")
    op.drop_table("chunks")

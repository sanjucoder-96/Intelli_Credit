"""Schema-level assertions on the ORM metadata (no database required)."""

from __future__ import annotations

import app.infrastructure.db.models  # noqa: F401 — populates Base.metadata
from app.infrastructure.db.base import Base

EXPECTED_TABLES = {
    # Phase 2
    "organizations",
    "users",
    "companies",
    "loan_applications",
    "documents",
    "extracted_fields",
    "evidence",
    "research_snapshots",
    "workflows",
    "workflow_executions",
    "agent_executions",
    "llm_interactions",
    "risk_assessments",
    "reports",
    "audit_logs",
    "feature_flags",
    "ml_models",
    # Phase 3
    "chunks",
    "memory_items",
    "document_stages",
    "findings",
    "scorecard_configs",
    "score_breakdowns",
    "agent_execution_payloads",
}


def test_all_expected_tables_are_mapped() -> None:
    assert set(Base.metadata.tables) >= EXPECTED_TABLES, sorted(
        EXPECTED_TABLES - set(Base.metadata.tables)
    )


def test_every_table_has_uuid_pk_and_timestamps() -> None:
    for name in EXPECTED_TABLES:
        table = Base.metadata.tables[name]
        pk_columns = list(table.primary_key.columns)
        assert len(pk_columns) == 1 and pk_columns[0].name == "id", name
        assert "created_at" in table.c, name
        assert "updated_at" in table.c, name


def test_every_tenant_scoped_table_indexes_org_id() -> None:
    for name in EXPECTED_TABLES:
        table = Base.metadata.tables[name]
        if "org_id" not in table.c:
            continue
        indexed = any("org_id" in {col.name for col in index.columns} for index in table.indexes)
        assert indexed, f"{name}.org_id has no index"


def test_workflow_versions_unique() -> None:
    workflows = Base.metadata.tables["workflows"]
    unique_sets = [
        {col.name for col in constraint.columns}
        for constraint in workflows.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"key", "version"} in unique_sets


def test_ml_models_versions_unique() -> None:
    ml_models = Base.metadata.tables["ml_models"]
    unique_sets = [
        {col.name for col in constraint.columns}
        for constraint in ml_models.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"name", "version"} in unique_sets


def test_soft_delete_only_where_appropriate() -> None:
    soft_deletable = {"users", "companies", "loan_applications", "documents", "reports"}
    for name in soft_deletable:
        assert "deleted_at" in Base.metadata.tables[name].c, name
    # Append-only/audit tables must never soft-delete.
    for name in (
        "audit_logs",
        "evidence",
        "workflows",
        "agent_executions",
        "findings",
        "chunks",
        "scorecard_configs",
        "score_breakdowns",
    ):
        assert "deleted_at" not in Base.metadata.tables[name].c, name

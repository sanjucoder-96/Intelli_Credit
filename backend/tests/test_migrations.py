"""Alembic migration round-trip against live Postgres (integration).

Runs only with ``-m integration`` (CI services / compose test profile).
Uses a subprocess so Alembic's async env owns its own event loop, isolated
from pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    # Phase 2 (0001)
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
    # Phase 3 (0002)
    "chunks",
    "memory_items",
    "document_stages",
    "findings",
    "scorecard_configs",
    "score_breakdowns",
    "agent_execution_payloads",
}


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )


async def _fetch_state() -> tuple[set[str], bool]:
    engine = create_async_engine(os.environ["IC_DATABASE__URL"])
    try:
        async with engine.connect() as conn:
            tables = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            vector = await conn.execute(
                text("SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'")
            )
            return {row[0] for row in tables}, bool(vector.scalar())
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("IC_INTEGRATION_TESTS"),
    reason="live Postgres required — set IC_INTEGRATION_TESTS=1 (CI does; see Makefile)",
)
def test_migration_upgrade_and_downgrade_roundtrip() -> None:
    upgrade = _alembic("upgrade", "head")
    assert upgrade.returncode == 0, f"upgrade failed:\n{upgrade.stdout}\n{upgrade.stderr}"

    tables, vector_enabled = asyncio.run(_fetch_state())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"tables missing after upgrade: {sorted(missing)}"
    assert vector_enabled, "pgvector extension not enabled by the initial migration"

    downgrade = _alembic("downgrade", "base")
    assert downgrade.returncode == 0, f"downgrade failed:\n{downgrade.stdout}\n{downgrade.stderr}"

    tables_after, _ = asyncio.run(_fetch_state())
    left_behind = EXPECTED_TABLES & tables_after
    assert not left_behind, f"tables left behind after downgrade: {sorted(left_behind)}"

    # Leave the schema in place for subsequent integration tests / manual runs.
    final = _alembic("upgrade", "head")
    assert final.returncode == 0, final.stderr

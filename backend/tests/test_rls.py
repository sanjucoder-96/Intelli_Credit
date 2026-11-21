"""Two-layer tenant isolation (ADR-0013): repository scoping AND Postgres RLS.

The acceptance test the ADR demands: cross-tenant leakage must require two
simultaneous bugs. Layer 1 (repository ``scoped()``) is proven with RLS
bypassed (admin superuser connection); layer 2 (RLS policies on the app
role) is proven with repository scoping bypassed (raw SQL). Policy coverage
is asserted from ``pg_class``/``pg_policies`` for every org-carrying table.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa

from app.core.context import TenantContext
from app.domain.enums import DataOrigin, Role
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Company, Organization
from app.infrastructure.db.repositories.companies import CompanyRepository
from tests.support import admin_engine, rls_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]


async def _seed_two_orgs() -> tuple[TenantContext, TenantContext, uuid.UUID, uuid.UUID]:
    """Create two orgs, one company each; return (ctx_a, ctx_b, co_a, co_b)."""
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            suffix = uuid.uuid4().hex[:8]
            org_a = Organization(name=f"Org A {suffix}", slug=f"org-a-{suffix}", is_active=True)
            org_b = Organization(name=f"Org B {suffix}", slug=f"org-b-{suffix}", is_active=True)
            session.add_all([org_a, org_b])
            await session.flush()
            company_a = Company(
                org_id=org_a.id,
                legal_name=f"Alpha Mills {suffix}",
                data_origin=DataOrigin.SYNTHETIC_DEMO,
            )
            company_b = Company(
                org_id=org_b.id,
                legal_name=f"Beta Traders {suffix}",
                data_origin=DataOrigin.SYNTHETIC_DEMO,
            )
            session.add_all([company_a, company_b])
            await session.commit()
            ctx_a = TenantContext(org_id=org_a.id, user_id=uuid.uuid4(), role=Role.ANALYST)
            ctx_b = TenantContext(org_id=org_b.id, user_id=uuid.uuid4(), role=Role.ANALYST)
            return ctx_a, ctx_b, company_a.id, company_b.id


async def test_layer1_repository_scoping_blocks_cross_tenant_reads() -> None:
    """With RLS bypassed (superuser), the repository choke point must hold."""
    ctx_a, _ctx_b, company_a_id, company_b_id = await _seed_two_orgs()
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            repo = CompanyRepository(session)

            assert await repo.get(ctx_a, company_a_id) is not None
            assert await repo.get(ctx_a, company_b_id) is None  # the layer under test

            # Control: an unscoped raw query CAN see the foreign row on this
            # superuser connection — proving this test detects leaks and that
            # only the repository layer blocked the read above.
            leak = await session.execute(sa.select(Company.id).where(Company.id == company_b_id))
            assert leak.scalar_one() == company_b_id


async def test_layer2_rls_blocks_cross_tenant_reads_without_repo_scoping() -> None:
    """With repository scoping bypassed (raw SQL), RLS on the app role holds."""
    ctx_a, ctx_b, company_a_id, company_b_id = await _seed_two_orgs()
    async with rls_engine() as engine:
        factory = session_factory(engine)

        async def rows_visible(ctx: TenantContext | None, company_id: uuid.UUID) -> int:
            async with factory() as session, session.begin():
                if ctx is not None:
                    await session.execute(
                        sa.select(sa.func.set_config("app.current_org_id", str(ctx.org_id), True))
                    )
                result = await session.execute(
                    sa.text("SELECT count(*) FROM companies WHERE id = :cid"),
                    {"cid": str(company_id)},
                )
                return int(result.scalar_one())

        assert await rows_visible(ctx_a, company_a_id) == 1  # own row visible
        assert await rows_visible(ctx_a, company_b_id) == 0  # foreign row invisible
        assert await rows_visible(ctx_b, company_b_id) == 1
        assert await rows_visible(None, company_a_id) == 0  # no GUC → default deny


async def test_rls_with_check_blocks_cross_tenant_writes() -> None:
    """A tenant session cannot INSERT rows stamped with another org."""
    ctx_a, ctx_b, _co_a, _co_b = await _seed_two_orgs()
    async with rls_engine() as engine:
        factory = session_factory(engine)
        with pytest.raises(sa.exc.DBAPIError):
            async with factory() as session, session.begin():
                await session.execute(
                    sa.select(sa.func.set_config("app.current_org_id", str(ctx_a.org_id), True))
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO companies (id, org_id, legal_name, data_origin) "
                        "VALUES (:id, :org, :name, 'synthetic_demo')"
                    ),
                    {"id": str(uuid.uuid4()), "org": str(ctx_b.org_id), "name": "Smuggled Co"},
                )


async def test_every_org_table_has_forced_rls_policy() -> None:
    """Coverage: every table carrying org_id is RLS-enabled, FORCEd, policied."""
    import app.infrastructure.db.models  # noqa: F401 - populate metadata

    org_tables = sorted(name for name, table in Base.metadata.tables.items() if "org_id" in table.c)
    assert org_tables, "expected org-scoped tables in metadata"

    async with admin_engine() as engine, engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                """
                    SELECT c.relname,
                           c.relrowsecurity,
                           c.relforcerowsecurity,
                           count(p.policyname) AS policies
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                    LEFT JOIN pg_policies p
                           ON p.tablename = c.relname AND p.schemaname = 'public'
                    WHERE c.relname = ANY(:tables)
                    GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
                    """
            ),
            {"tables": org_tables},
        )
        rows = {row.relname: row for row in result}

    missing = [name for name in org_tables if name not in rows]
    assert not missing, f"tables absent from pg_class: {missing}"
    problems = {
        name: {
            "rls": row.relrowsecurity,
            "forced": row.relforcerowsecurity,
            "policies": row.policies,
        }
        for name, row in rows.items()
        if not (row.relrowsecurity and row.relforcerowsecurity and row.policies >= 1)
    }
    assert not problems, f"org tables without forced RLS + policy: {problems}"

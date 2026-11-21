"""Phase-8 security — tenant isolation + upload guardrails (priority #8, integration).

Realistic IntelliCredit attack paths that need a live database:

- RLS blocks a tenant from seeing another tenant's cases (default-deny, and a
  session with no org context sees nothing);
- the repository layer refuses a cross-tenant case lookup;
- the upload handler rejects disallowed extensions, oversized files, and empty
  submissions.
"""

from __future__ import annotations

import io
import os
import uuid

import pytest
import sqlalchemy as sa
from fastapi import UploadFile

from app.api.routers.cases import upload_documents
from app.core.config import get_settings
from app.core.context import TenantContext
from app.core.errors import ValidationFailedError
from app.domain.enums import DataOrigin, Role
from app.infrastructure.db.models import Company, LoanApplication, Organization
from app.infrastructure.db.repositories.cases import LoanApplicationRepository
from app.infrastructure.storage.local import LocalFilesystemStorage
from tests.support import admin_engine, rls_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]


async def _seed_two_orgs(
    engine: object,
) -> tuple[TenantContext, TenantContext, uuid.UUID, uuid.UUID]:
    factory = session_factory(engine)  # type: ignore[arg-type]
    async with factory() as session:
        cases: list[uuid.UUID] = []
        ctxs: list[TenantContext] = []
        for tag in ("a", "b"):
            s = uuid.uuid4().hex[:8]
            org = Organization(name=f"Org {tag} {s}", slug=f"org-{tag}-{s}", is_active=True)
            session.add(org)
            await session.flush()
            company = Company(
                org_id=org.id, legal_name=f"Co {tag} {s}", data_origin=DataOrigin.SYNTHETIC_DEMO
            )
            session.add(company)
            await session.flush()
            case = LoanApplication(org_id=org.id, company_id=company.id, title=f"Case {tag}")
            session.add(case)
            await session.flush()
            cases.append(case.id)
            ctxs.append(TenantContext(org_id=org.id, user_id=uuid.uuid4(), role=Role.ANALYST))
        await session.commit()
        return ctxs[0], ctxs[1], cases[0], cases[1]


async def test_rls_blocks_cross_tenant_case_reads_and_defaults_deny() -> None:
    async with admin_engine() as admin:
        ctx_a, ctx_b, case_a, case_b = await _seed_two_orgs(admin)

    async with rls_engine() as engine:
        factory = session_factory(engine)

        async def visible(ctx: TenantContext | None, case_id: uuid.UUID) -> int:
            async with factory() as session, session.begin():
                if ctx is not None:
                    await session.execute(
                        sa.select(sa.func.set_config("app.current_org_id", str(ctx.org_id), True))
                    )
                return int(
                    (
                        await session.execute(
                            sa.text("SELECT count(*) FROM loan_applications WHERE id = :cid"),
                            {"cid": str(case_id)},
                        )
                    ).scalar_one()
                )

        assert await visible(ctx_a, case_a) == 1  # own case visible
        assert await visible(ctx_a, case_b) == 0  # foreign case invisible
        assert await visible(ctx_b, case_a) == 0
        assert await visible(None, case_a) == 0  # no org context → default deny


async def test_repository_refuses_cross_tenant_case_lookup() -> None:
    async with admin_engine() as engine:
        ctx_a, ctx_b, case_a, _case_b = await _seed_two_orgs(engine)
        factory = session_factory(engine)
        async with factory() as session:
            repo = LoanApplicationRepository(session)
            assert await repo.get(ctx_a, case_a) is not None  # own case
            assert await repo.get(ctx_b, case_a) is None  # foreign case refused by scoping


def _upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


async def test_upload_rejects_disallowed_extensions(tmp_path) -> None:  # noqa: ANN001
    async with admin_engine() as engine:
        ctx_a, _ctx_b, case_a, _case_b = await _seed_two_orgs(engine)
        factory = session_factory(engine)
        storage = LocalFilesystemStorage(tmp_path)
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await upload_documents(
                    case_a,
                    [_upload("malware.exe", b"MZ\x90\x00")],
                    ctx_a,
                    session,
                    storage,
                    get_settings(),
                )


async def test_upload_rejects_oversized_files(tmp_path) -> None:  # noqa: ANN001
    async with admin_engine() as engine:
        ctx_a, _ctx_b, case_a, _case_b = await _seed_two_orgs(engine)
        factory = session_factory(engine)
        storage = LocalFilesystemStorage(tmp_path)
        small_cap = get_settings().model_copy(
            update={"ingest": get_settings().ingest.model_copy(update={"max_upload_mb": 1})}
        )
        two_mb = b"%PDF-1.3\n" + b"0" * (2 * 1024 * 1024)
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await upload_documents(
                    case_a, [_upload("big.pdf", two_mb)], ctx_a, session, storage, small_cap
                )


async def test_upload_rejects_empty_submission(tmp_path) -> None:  # noqa: ANN001
    async with admin_engine() as engine:
        ctx_a, _ctx_b, case_a, _case_b = await _seed_two_orgs(engine)
        factory = session_factory(engine)
        storage = LocalFilesystemStorage(tmp_path)
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await upload_documents(case_a, [], ctx_a, session, storage, get_settings())

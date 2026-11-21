"""Company endpoints (Phase 4): create + list, tenant-scoped two-layer."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.providers import get_tenant_context, get_tenant_db_session, require_permissions
from app.api.schemas.cases import CompanyCreateRequest, CompanyResponse
from app.core.context import TenantContext
from app.domain.enums import Permission
from app.infrastructure.db.repositories.audit import record_audit
from app.infrastructure.db.repositories.companies import CompanyRepository

router = APIRouter(prefix="/companies", tags=["companies"])


@router.post(
    "",
    response_model=CompanyResponse,
    status_code=201,
    dependencies=[Depends(require_permissions(Permission.CASE_WRITE))],
)
async def create_company(
    body: CompanyCreateRequest,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> CompanyResponse:
    """Create a borrower company (CIN/GSTIN/PAN validated as Indian formats)."""
    company = await CompanyRepository(session).create(
        ctx,
        legal_name=body.legal_name,
        cin=body.cin,
        gstin=body.gstin,
        pan=body.pan,
        sector=body.sector,
    )
    await record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="company.create",
        entity_type="company",
        entity_id=company.id,
        payload={"legal_name": company.legal_name},
    )
    return CompanyResponse.model_validate(company.model_dump())


@router.get(
    "",
    response_model=list[CompanyResponse],
    dependencies=[Depends(require_permissions(Permission.CASE_READ))],
)
async def list_companies(
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
    limit: int = 50,
    offset: int = 0,
) -> list[CompanyResponse]:
    """List the org's companies (paginated)."""
    companies = await CompanyRepository(session).list(
        ctx, limit=min(limit, 200), offset=max(offset, 0)
    )
    return [CompanyResponse.model_validate(company.model_dump()) for company in companies]

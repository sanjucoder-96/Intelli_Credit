"""Company repository implementing
:class:`app.application.ports.CompanyRepositoryPort`.

Skeleton read paths only — Phase 3 adds create/update with identifier
validation (CIN/GSTIN/PAN value objects) and document linkage. Every
statement routes through :meth:`BaseRepository.scoped`, the tenant choke
point (ADR-0013).
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa

from app.core.context import TenantContext
from app.domain.entities import CompanyEntity
from app.infrastructure.db.models.companies import Company
from app.infrastructure.db.repositories.base import BaseRepository


class CompanyRepository(BaseRepository[Company]):
    """Async SQLAlchemy adapter for borrower-company persistence."""

    model = Company

    async def create(
        self,
        ctx: TenantContext,
        *,
        legal_name: str,
        cin: str | None = None,
        gstin: str | None = None,
        pan: str | None = None,
        sector: str | None = None,
    ) -> CompanyEntity:
        company = Company(
            org_id=ctx.org_id,
            legal_name=legal_name,
            cin=cin,
            gstin=gstin,
            pan=pan,
            sector=sector,
        )
        self._session.add(company)
        await self._session.flush()
        return CompanyEntity.model_validate(company)

    async def get(self, ctx: TenantContext, company_id: UUID) -> CompanyEntity | None:
        """Tenant-scoped lookup by primary key (soft-deleted rows excluded)."""
        row = await self.get_row(ctx, company_id)
        return CompanyEntity.model_validate(row) if row is not None else None

    async def list(
        self, ctx: TenantContext, *, limit: int = 50, offset: int = 0
    ) -> list[CompanyEntity]:
        """Tenant-scoped paginated listing ordered by legal name."""
        stmt = (
            self.scoped(sa.select(Company), ctx)
            .order_by(Company.legal_name)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [CompanyEntity.model_validate(row) for row in result.scalars().all()]

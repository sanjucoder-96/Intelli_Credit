"""Loan-application (case) and document repositories — Phase-4 API surface.

Every statement routes through :meth:`BaseRepository.scoped` (ADR-0013);
authenticated request paths additionally run on the RLS tenant session, so
isolation is two-layer end to end.
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa

from app.core.context import TenantContext
from app.domain.entities import LoanApplicationEntity
from app.domain.enums import DataOrigin, LoanApplicationStatus
from app.infrastructure.db.models import Document, DocumentStage, LoanApplication
from app.infrastructure.db.repositories.base import BaseRepository


class LoanApplicationRepository(BaseRepository[LoanApplication]):
    """Case persistence."""

    model = LoanApplication

    async def create(
        self,
        ctx: TenantContext,
        *,
        company_id: UUID,
        title: str,
        requested_amount: object | None = None,
        currency: str = "INR",
    ) -> LoanApplicationEntity:
        case = LoanApplication(
            org_id=ctx.org_id,
            company_id=company_id,
            title=title,
            status=LoanApplicationStatus.DRAFT,
            requested_amount=requested_amount,
            currency=currency,
            created_by=ctx.user_id,
        )
        self._session.add(case)
        await self._session.flush()
        return LoanApplicationEntity.model_validate(case)

    async def get(self, ctx: TenantContext, case_id: UUID) -> LoanApplicationEntity | None:
        row = await self.get_row(ctx, case_id)
        return LoanApplicationEntity.model_validate(row) if row is not None else None

    async def list(
        self, ctx: TenantContext, *, limit: int = 50, offset: int = 0
    ) -> list[LoanApplicationEntity]:
        stmt = (
            self.scoped(sa.select(LoanApplication), ctx)
            .order_by(LoanApplication.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [LoanApplicationEntity.model_validate(row) for row in result.scalars()]


class DocumentRepository(BaseRepository[Document]):
    """Document persistence + stage checkpoints for the progress UI."""

    model = Document

    async def create(
        self,
        ctx: TenantContext,
        *,
        loan_application_id: UUID,
        company_id: UUID | None,
        storage_key: str,
        sha256: str,
        filename: str,
        media_type: str,
        data_origin: DataOrigin = DataOrigin.REAL,
    ) -> Document:
        document = Document(
            org_id=ctx.org_id,
            loan_application_id=loan_application_id,
            company_id=company_id,
            storage_key=storage_key,
            sha256=sha256,
            filename=filename,
            media_type=media_type,
            data_origin=data_origin,
        )
        self._session.add(document)
        await self._session.flush()
        return document

    async def get(self, ctx: TenantContext, document_id: UUID) -> Document | None:
        return await self.get_row(ctx, document_id)

    async def list_by_case(self, ctx: TenantContext, case_id: UUID) -> list[Document]:
        stmt = self.scoped(
            sa.select(Document).where(Document.loan_application_id == case_id), ctx
        ).order_by(Document.created_at)
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def stages_for_case(
        self, ctx: TenantContext, case_id: UUID
    ) -> list[tuple[UUID, DocumentStage]]:
        """(document_id, stage checkpoint) pairs for the SSE snapshot/rehydrate."""
        stmt = (
            sa.select(Document.id, DocumentStage)
            .join(DocumentStage, DocumentStage.document_id == Document.id)
            .where(Document.loan_application_id == case_id)
        )
        # Document carries org_id; scope through the choke point manually here
        # because of the join shape.
        if ctx is not None:
            stmt = stmt.where(Document.org_id == ctx.org_id, Document.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

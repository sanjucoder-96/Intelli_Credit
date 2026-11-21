"""API schemas for companies, cases, documents, search, and flags (Phase 4)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import (
    DocumentStatus,
    FlagScope,
    IngestStage,
    LoanApplicationStatus,
    StageStatus,
)
from app.domain.value_objects import CIN, GSTIN, PAN


class CompanyCreateRequest(BaseModel):
    """Create a borrower company; identifiers validate as Indian formats."""

    legal_name: str = Field(min_length=2, max_length=512)
    cin: CIN | None = None
    gstin: GSTIN | None = None
    pan: PAN | None = None
    sector: str | None = Field(default=None, max_length=128)


class CompanyResponse(BaseModel):
    id: UUID
    legal_name: str
    cin: str | None = None
    gstin: str | None = None
    pan: str | None = None
    sector: str | None = None
    created_at: datetime


class CaseCreateRequest(BaseModel):
    company_id: UUID
    title: str = Field(min_length=3, max_length=256)
    requested_amount: Decimal | None = Field(default=None, gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)


class CaseResponse(BaseModel):
    id: UUID
    company_id: UUID
    title: str
    status: LoanApplicationStatus
    requested_amount: Decimal | None = None
    currency: str
    created_at: datetime


class StageStatusResponse(BaseModel):
    stage: IngestStage
    status: StageStatus
    attempt: int
    error: dict[str, Any] | None = None


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    media_type: str
    doc_type: str
    doc_type_confidence: float | None = None
    status: DocumentStatus
    page_count: int | None = None
    sha256: str
    created_at: datetime
    stages: list[StageStatusResponse] = Field(default_factory=list)


class UploadResponse(BaseModel):
    documents: list[DocumentResponse]


class DocumentPasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=512)
    top_k: int = Field(default=10, ge=1, le=50)
    doc_types: list[str] | None = None


class SearchHit(BaseModel):
    chunk_id: UUID
    document_id: UUID
    doc_type: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    rrf_score: float
    fts_rank: int | None = None
    vector_rank: int | None = None


class SearchResponse(BaseModel):
    hits: list[SearchHit]


class FlagSetRequest(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    scope: FlagScope = FlagScope.GLOBAL
    enabled: bool
    org_id: UUID | None = None
    workflow_key: str | None = Field(default=None, max_length=64)


class FlagResponse(BaseModel):
    key: str
    scope: str
    org_id: str | None = None
    workflow_key: str | None = None
    enabled: bool
    value: dict[str, Any] | None = None

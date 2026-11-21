"""Domain read-models mapped from ORM rows by repositories (ARCHITECTURE §3).

Thin pydantic models (``from_attributes=True``) that decouple use cases and
API responses from SQLAlchemy instances. Phase 3+ adds DocumentEntity,
EvidenceEntity, and the workflow-run read models as their endpoints land.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import DataOrigin, LoanApplicationStatus, Role


class UserEntity(BaseModel):
    """Read model of a platform user."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime


class CompanyEntity(BaseModel):
    """Read model of a borrower company."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    legal_name: str
    cin: str | None = None
    gstin: str | None = None
    pan: str | None = None
    sector: str | None = None
    data_origin: DataOrigin
    created_at: datetime


class LoanApplicationEntity(BaseModel):
    """Read model of a loan application (the ARCHITECTURE 'Case')."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    company_id: UUID
    workflow_key: str | None = None
    title: str
    status: LoanApplicationStatus
    requested_amount: Decimal | None = None
    currency: str
    created_at: datetime

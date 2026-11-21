"""API schemas for workflow runs + HITL review (Phase 5).

A run is started asynchronously (the worker owns execution over the durable
checkpointer), so ``StartRunResponse`` returns the ``run_id`` to poll. The
officer's HITL submission carries only qualitative *notches* — bounded nudges
the deterministic scorecard applies (never a score, limit, or rate the human
types in): the numbers stay with the engine (invariant #3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import WorkflowExecutionStatus


class StartRunRequest(BaseModel):
    """Start a workflow run for a case (defaults to credit appraisal)."""

    workflow_key: str = Field(default="credit_appraisal", max_length=64)
    version: int | None = None
    # Financial line items assembled from the case's extracted statements; the
    # engine also reads them from ingestion when omitted here.
    financials: dict[str, Any] | None = None
    scratch: dict[str, Any] | None = None


class StartRunResponse(BaseModel):
    run_id: str
    workflow_key: str
    status: str = "queued"


class HitlNote(BaseModel):
    """One officer qualitative note → a bounded scorecard notch (±12 clamp)."""

    label: str = Field(min_length=1, max_length=200)
    notch: float = Field(ge=-12.0, le=12.0)


class HitlSubmitRequest(BaseModel):
    """Officer review input that resumes a paused run."""

    notes: list[HitlNote] = Field(default_factory=list)
    knockouts: list[str] = Field(default_factory=list)


class RunStatusResponse(BaseModel):
    run_id: str
    workflow_key: str
    status: WorkflowExecutionStatus
    cost_usd: float
    grade: str | None = None
    decision: str | None = None
    report_id: UUID | None = None
    error: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunReportResponse(BaseModel):
    report_id: UUID
    report_type: str
    grade: str | None = None
    decision: str | None = None
    markdown: str
    ir: dict[str, Any]

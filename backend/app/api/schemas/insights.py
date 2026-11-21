"""Read-model schemas for the Phase-7 frontend (case insights, run detail,
findings, dashboard).

These are projections over already-persisted rows — WorkflowExecution,
RiskAssessment, ScoreBreakdown, Report, Finding, Evidence — assembled for the
UI. They add no business logic: every number originates in a backend row
(ADR-0009/0010; the frontend never recomputes a score, limit, rate, or PD).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import WorkflowExecutionStatus


class RunSummary(BaseModel):
    """One workflow run for a case, flattened with its risk/report outcome."""

    run_id: str
    workflow_key: str
    status: WorkflowExecutionStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cost_usd: float = 0.0
    grade: str | None = None
    decision: str | None = None
    final_score: float | None = None
    coverage: float | None = None
    recommended_limit: str | None = None
    interest_rate_pct: float | None = None
    pd: float | None = None
    non_production_ml: bool = False
    report_id: UUID | None = None
    # Demo-reliability signal: LIVE | REPLAY | DEGRADED (truthful about fallback).
    execution_mode: str = "LIVE"


class RiskFactorView(BaseModel):
    pillar_id: str
    factor_id: str
    raw_value: float | None = None
    normalized: float | None = None
    weight: float
    contribution: float
    admitted: bool
    exclusion_reason: str | None = None
    explanation: str = ""


class OverlayView(BaseModel):
    rule_id: str
    fired: bool
    effect: str
    detail: str = ""


class MlPredictionView(BaseModel):
    available: bool = False
    pd: float | None = None
    pd_raw: float | None = None
    coverage: float = 0.0
    model_name: str | None = None
    model_version: str | None = None
    algorithm: str | None = None
    feature_schema_version: str | None = None
    non_production: bool = False
    top_risk_factors: list[dict[str, Any]] = Field(default_factory=list)
    top_mitigants: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class AssessmentView(BaseModel):
    scorecard_key: str
    scorecard_version: str
    base_score: float | None = None
    final_score: float | None = None
    grade: str | None = None
    decision: str | None = None
    coverage: float | None = None
    factors: list[RiskFactorView] = Field(default_factory=list)
    overlays: list[OverlayView] = Field(default_factory=list)
    limit: dict[str, Any] = Field(default_factory=dict)
    pricing: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    ml: MlPredictionView = Field(default_factory=MlPredictionView)


class NodeStatusView(BaseModel):
    node: str
    agent: str | None = None
    status: str  # ok | error | running | pending
    seq: int | None = None


class RunDetail(BaseModel):
    run_id: str
    workflow_key: str
    status: WorkflowExecutionStatus
    cost_usd: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: dict[str, Any] | None = None
    report_id: UUID | None = None
    assessment: AssessmentView | None = None
    nodes: list[NodeStatusView] = Field(default_factory=list)


class EvidenceView(BaseModel):
    id: UUID
    kind: str
    claim: str
    document_id: UUID | None = None
    page: int | None = None
    bboxes: dict[str, Any] | None = None
    url: str | None = None
    quote: str | None = None
    source_tier: float | None = None
    confidence: float | None = None


class FindingView(BaseModel):
    id: UUID
    agent: str | None = None
    kind: str
    category: str | None = None
    severity: str
    title: str
    narrative: str | None = None
    data: dict[str, Any] | None = None
    confidence: float | None = None
    source_tier: float | None = None
    created_at: datetime


class CaseFindings(BaseModel):
    findings: list[FindingView] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)


class RecentDecision(BaseModel):
    case_id: UUID
    company_legal_name: str
    run_id: str
    grade: str | None = None
    decision: str | None = None
    final_score: float | None = None
    finished_at: datetime | None = None


class GradeBucket(BaseModel):
    grade: str
    count: int


class ExtractedFieldView(BaseModel):
    id: UUID
    document_id: UUID
    schema_id: str
    field_path: str
    value: Any = None
    confidence: float | None = None
    page: int | None = None
    bbox: dict[str, Any] | None = None


class FinancialSource(BaseModel):
    field: str
    value: str
    document_id: UUID
    filename: str | None = None
    page: int | None = None
    confidence: float | None = None


class FinancialsAssembly(BaseModel):
    """Financial line items assembled from the case's extracted fields.

    A best-effort projection over ``extracted_fields`` — the analyst confirms
    these before starting an appraisal. ``available`` is False when no financial
    statement has been extracted yet (the analyst then enters figures manually).
    """

    available: bool = False
    fiscal_year: str | None = None
    financials: dict[str, str] = Field(default_factory=dict)
    sources: list[FinancialSource] = Field(default_factory=list)


class DashboardSummary(BaseModel):
    active_cases: int = 0
    awaiting_review: int = 0
    completed_runs: int = 0
    running: int = 0
    failed: int = 0
    recent_decisions: list[RecentDecision] = Field(default_factory=list)
    grade_distribution: list[GradeBucket] = Field(default_factory=list)
    decision_distribution: dict[str, int] = Field(default_factory=dict)

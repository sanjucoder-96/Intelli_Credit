"""Read-model endpoints for the Phase-7 frontend.

Projections over persisted rows for the case workspace, run detail (scorecard +
decision + ML), findings/evidence, and the dashboard. Read-only, RLS-scoped, and
free of business logic — the numbers are served exactly as the workflow engine
persisted them (ADR-0009/0010). No score/limit/rate/PD is computed here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.providers import get_tenant_db_session, require_permissions
from app.api.schemas.insights import (
    AssessmentView,
    CaseFindings,
    DashboardSummary,
    EvidenceView,
    ExtractedFieldView,
    FinancialsAssembly,
    FinancialSource,
    FindingView,
    GradeBucket,
    MlPredictionView,
    NodeStatusView,
    OverlayView,
    RecentDecision,
    RiskFactorView,
    RunDetail,
    RunSummary,
)
from app.application.execution_mode import resolve_execution_mode
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.docintel.schemas import FinancialLineItems
from app.domain.enums import ExecutionStatus, Permission, WorkflowExecutionStatus
from app.infrastructure.db.models import (
    AgentExecution,
    Company,
    Document,
    Evidence,
    ExtractedField,
    Finding,
    LLMInteraction,
    LoanApplication,
    Report,
    RiskAssessment,
    Workflow,
    WorkflowExecution,
)

_FINANCIAL_FIELDS = set(FinancialLineItems.model_fields)

router = APIRouter(tags=["insights"])

_READ = Depends(require_permissions(Permission.CASE_READ))


def _assessment_view(a: RiskAssessment) -> AssessmentView:
    explanation = a.explanation or {}
    breakdown = a.breakdown or {}
    overlays = a.overlay_log or {}
    return AssessmentView(
        scorecard_key=a.scorecard_key,
        scorecard_version=a.scorecard_version,
        base_score=a.base_score,
        final_score=a.final_score,
        grade=a.grade,
        decision=a.decision.value if a.decision else None,
        coverage=a.coverage,
        factors=[RiskFactorView.model_validate(f) for f in breakdown.get("factors", [])],
        overlays=[OverlayView.model_validate(o) for o in overlays.get("overlays", [])],
        limit=explanation.get("limit", {}) or {},
        pricing=explanation.get("pricing", {}) or {},
        notes=explanation.get("notes", []) or [],
        ml=MlPredictionView.model_validate(explanation.get("ml", {}) or {}),
    )


def _run_summary(
    execution: WorkflowExecution,
    assessment: RiskAssessment | None,
    report_id: UUID | None,
    execution_mode: str = "LIVE",
) -> RunSummary:
    explanation = (assessment.explanation if assessment else None) or {}
    ml = explanation.get("ml", {}) or {}
    limit = explanation.get("limit", {}) or {}
    pricing = explanation.get("pricing", {}) or {}
    return RunSummary(
        run_id=execution.thread_id or str(execution.id),
        workflow_key="",  # filled by caller when the workflow row is loaded
        status=execution.status,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        cost_usd=float(execution.cost_usd),
        grade=assessment.grade if assessment else None,
        decision=assessment.decision.value if assessment and assessment.decision else None,
        final_score=assessment.final_score if assessment else None,
        coverage=assessment.coverage if assessment else None,
        recommended_limit=str(limit["amount"]) if limit.get("amount") is not None else None,
        interest_rate_pct=pricing.get("rate_pct"),
        pd=ml.get("pd"),
        non_production_ml=bool(ml.get("non_production", False)),
        report_id=report_id,
        execution_mode=execution_mode,
    )


async def _run_degraded(session: AsyncSession, execution: WorkflowExecution) -> bool:
    """A run is degraded if it errored or any LLM ledger row was tagged (e.g. a
    transient-503 replay fallback) — surfaced truthfully, never as a clean LIVE."""
    if execution.error is not None:
        return True
    return bool(
        await session.scalar(
            sa.select(
                sa.exists().where(
                    LLMInteraction.workflow_execution_id == execution.id,
                    LLMInteraction.error.isnot(None),
                )
            )
        )
    )


async def _assessment_for(session: AsyncSession, execution_id: UUID) -> RiskAssessment | None:
    return (
        await session.execute(
            sa.select(RiskAssessment).where(RiskAssessment.workflow_execution_id == execution_id)
        )
    ).scalar_one_or_none()


async def _report_id_for(session: AsyncSession, execution_id: UUID) -> UUID | None:
    return (
        await session.execute(
            sa.select(Report.id).where(Report.workflow_execution_id == execution_id)
        )
    ).scalar_one_or_none()


async def _workflow_key(session: AsyncSession, workflow_id: UUID) -> str:
    wf = await session.get(Workflow, workflow_id)
    return wf.key if wf else ""


@router.get("/cases/{case_id}/runs", response_model=list[RunSummary], dependencies=[_READ])
async def list_case_runs(
    case_id: UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> list[RunSummary]:
    """Every workflow run for a case, newest first, with its risk/report outcome."""
    executions = list(
        (
            await session.execute(
                sa.select(WorkflowExecution)
                .where(WorkflowExecution.loan_application_id == case_id)
                .order_by(WorkflowExecution.created_at.desc())
            )
        ).scalars()
    )
    llm_mode = get_settings().agent.llm_mode
    summaries: list[RunSummary] = []
    for execution in executions:
        assessment = await _assessment_for(session, execution.id)
        report_id = await _report_id_for(session, execution.id)
        mode = resolve_execution_mode(
            llm_mode, degraded=await _run_degraded(session, execution)
        )
        summary = _run_summary(execution, assessment, report_id, mode)
        summary.workflow_key = await _workflow_key(session, execution.workflow_id)
        summaries.append(summary)
    return summaries


@router.get("/runs/{run_id}/detail", response_model=RunDetail, dependencies=[_READ])
async def get_run_detail(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> RunDetail:
    """Full run detail: scorecard breakdown, overlays, limit/pricing, ML, nodes."""
    execution = (
        await session.execute(
            sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
        )
    ).scalar_one_or_none()
    if execution is None:
        raise NotFoundError(f"No workflow run '{run_id}'")

    assessment = await _assessment_for(session, execution.id)
    report_id = await _report_id_for(session, execution.id)

    # Per-node status from the agent-execution trace, ordered by first sequence.
    rows = list(
        (
            await session.execute(
                sa.select(
                    AgentExecution.node,
                    AgentExecution.agent,
                    AgentExecution.seq,
                    AgentExecution.status,
                )
                .where(AgentExecution.workflow_execution_id == execution.id)
                .order_by(AgentExecution.seq.asc())
            )
        ).all()
    )
    ran: dict[str, NodeStatusView] = {}
    for node, agent, seq, status in rows:
        current = ran.get(node)
        errored = status is ExecutionStatus.ERROR or (current and current.status == "error")
        ran[node] = NodeStatusView(
            node=node,
            agent=agent,
            seq=current.seq if current else seq,
            status="error" if errored else "ok",
        )
    # Overlay the workflow definition's node order so pending nodes show too. A
    # completed run's untraced nodes (e.g. the noop HITL gate skipped on resume)
    # are shown as done rather than pending.
    default_status = "ok" if execution.status is WorkflowExecutionStatus.COMPLETED else "pending"
    nodes: list[NodeStatusView] = []
    wf = await session.get(Workflow, execution.workflow_id)
    definition_nodes = wf.definition.get("nodes", []) if wf and wf.definition else []
    if definition_nodes:
        for spec in definition_nodes:
            name = spec.get("name")
            nodes.append(
                ran.get(name)
                or NodeStatusView(node=name, agent=spec.get("agent"), status=default_status)
            )
    else:
        nodes = sorted(ran.values(), key=lambda n: n.seq or 0)

    return RunDetail(
        run_id=run_id,
        workflow_key=wf.key if wf else "",
        status=execution.status,
        cost_usd=float(execution.cost_usd),
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        error=execution.error,
        report_id=report_id,
        assessment=_assessment_view(assessment) if assessment else None,
        nodes=nodes,
    )


@router.get("/cases/{case_id}/findings", response_model=CaseFindings, dependencies=[_READ])
async def list_case_findings(
    case_id: UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> CaseFindings:
    """Current (non-superseded) findings + the evidence ledger for a case."""
    findings = list(
        (
            await session.execute(
                sa.select(Finding)
                .where(
                    Finding.loan_application_id == case_id,
                    Finding.superseded_by_id.is_(None),
                )
                .order_by(Finding.created_at.desc())
            )
        ).scalars()
    )
    evidence = list(
        (
            await session.execute(
                sa.select(Evidence)
                .where(Evidence.loan_application_id == case_id)
                .order_by(Evidence.created_at.desc())
                .limit(200)
            )
        ).scalars()
    )
    return CaseFindings(
        findings=[
            FindingView(
                id=f.id,
                agent=f.agent,
                kind=f.kind.value,
                category=f.category.value if f.category else None,
                severity=f.severity.value,
                title=f.title,
                narrative=f.narrative,
                data=f.data,
                confidence=f.confidence,
                source_tier=f.source_tier,
                created_at=f.created_at,
            )
            for f in findings
        ],
        evidence=[
            EvidenceView(
                id=e.id,
                kind=e.kind.value,
                claim=e.claim,
                document_id=e.document_id,
                page=e.page,
                bboxes=e.bboxes,
                url=e.url,
                quote=e.quote,
                source_tier=e.source_tier,
                confidence=e.confidence,
            )
            for e in evidence
        ],
    )


@router.get(
    "/cases/{case_id}/documents/{document_id}/fields",
    response_model=list[ExtractedFieldView],
    dependencies=[_READ],
)
async def list_extracted_fields(
    case_id: UUID,
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> list[ExtractedFieldView]:
    """Current (non-superseded) extracted fields for a document — value/page/bbox."""
    rows = list(
        (
            await session.execute(
                sa.select(ExtractedField)
                .where(
                    ExtractedField.document_id == document_id,
                    ExtractedField.is_superseded.is_(False),
                )
                .order_by(ExtractedField.field_path.asc())
            )
        ).scalars()
    )
    return [
        ExtractedFieldView(
            id=f.id,
            document_id=f.document_id,
            schema_id=f.schema_id,
            field_path=f.field_path,
            value=(f.value or {}).get("value") if isinstance(f.value, dict) else f.value,
            confidence=f.confidence,
            page=f.page,
            bbox=f.bbox,
        )
        for f in rows
    ]


def _parse_financial_path(field_path: str) -> tuple[int, str] | None:
    """Map an extracted field path to (year_index, canonical_field), or None.

    Handles both nested statements (``years.0.revenue``) and flat schemas
    (``revenue``). Only fields on FinancialLineItems are admitted.
    """
    parts = field_path.split(".")
    if (
        len(parts) == 3
        and parts[0] == "years"
        and parts[1].isdigit()
        and parts[2] in _FINANCIAL_FIELDS
    ):
        return int(parts[1]), parts[2]
    if len(parts) == 1 and parts[0] in _FINANCIAL_FIELDS:
        return 0, parts[0]
    return None


@router.get("/cases/{case_id}/financials", response_model=FinancialsAssembly, dependencies=[_READ])
async def assemble_financials(
    case_id: UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> FinancialsAssembly:
    """Assemble FinancialLineItems from the case's extracted fields (best-effort).

    The frontend pre-fills the appraisal form with these; the analyst confirms.
    No number is invented — only extracted values are surfaced, each with its
    source document, page, and confidence.
    """
    rows = list(
        (
            await session.execute(
                sa.select(ExtractedField, Document.filename)
                .join(Document, ExtractedField.document_id == Document.id)
                .where(
                    Document.loan_application_id == case_id,
                    ExtractedField.is_superseded.is_(False),
                )
            )
        ).all()
    )
    # Group extracted values by fiscal-year index, keeping the highest-confidence
    # value per (year, field) and its provenance.
    by_year: dict[int, dict[str, tuple[str, FinancialSource]]] = {}
    fiscal_year_by_index: dict[int, str] = {}
    for field, filename in rows:
        raw = (field.value or {}).get("value") if isinstance(field.value, dict) else field.value
        if raw is None:
            continue
        # fiscal_year label per year index (nested or flat).
        parts = field.field_path.split(".")
        if (
            len(parts) == 3
            and parts[0] == "years"
            and parts[1].isdigit()
            and parts[2] == "fiscal_year"
        ):
            fiscal_year_by_index[int(parts[1])] = str(raw)
            continue
        if field.field_path == "fiscal_year":
            fiscal_year_by_index.setdefault(0, str(raw))
            continue
        parsed = _parse_financial_path(field.field_path)
        if parsed is None:
            continue
        year_idx, canonical = parsed
        slot = by_year.setdefault(year_idx, {})
        existing = slot.get(canonical)
        if existing is None or (field.confidence or 0) > (existing[1].confidence or 0):
            slot[canonical] = (
                str(raw),
                FinancialSource(
                    field=canonical,
                    value=str(raw),
                    document_id=field.document_id,
                    filename=filename,
                    page=field.page,
                    confidence=field.confidence,
                ),
            )

    if not by_year:
        return FinancialsAssembly(available=False)

    # Pick the fiscal year with the most populated line items (the primary period).
    best_idx = max(by_year, key=lambda i: len(by_year[i]))
    slot = by_year[best_idx]
    financials = {f: v[0] for f, v in slot.items() if f != "fiscal_year"}
    sources = [v[1] for v in slot.values()]
    return FinancialsAssembly(
        available=bool(financials),
        fiscal_year=fiscal_year_by_index.get(best_idx),
        financials=financials,
        sources=sources,
    )


@router.get("/dashboard/summary", response_model=DashboardSummary, dependencies=[_READ])
async def dashboard_summary(
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> DashboardSummary:
    """Org-wide portfolio snapshot for the dashboard."""

    async def _count(stmt: sa.Select[tuple[int]]) -> int:
        return int((await session.execute(stmt)).scalar_one())

    active_cases = await _count(
        sa.select(sa.func.count())
        .select_from(LoanApplication)
        .where(LoanApplication.deleted_at.is_(None))
    )

    status_counts: dict[str, int] = {}
    for status, count in (
        await session.execute(
            sa.select(WorkflowExecution.status, sa.func.count()).group_by(WorkflowExecution.status)
        )
    ).all():
        status_counts[status.value] = int(count)

    grade_rows = (
        await session.execute(
            sa.select(RiskAssessment.grade, sa.func.count())
            .where(RiskAssessment.grade.is_not(None))
            .group_by(RiskAssessment.grade)
        )
    ).all()
    grade_distribution = sorted(
        (GradeBucket(grade=g, count=int(c)) for g, c in grade_rows),
        key=lambda b: b.grade,
    )
    decision_rows = (
        await session.execute(
            sa.select(RiskAssessment.decision, sa.func.count())
            .where(RiskAssessment.decision.is_not(None))
            .group_by(RiskAssessment.decision)
        )
    ).all()
    decision_distribution = {d.value: int(c) for d, c in decision_rows if d is not None}

    recent = (
        await session.execute(
            sa.select(RiskAssessment, WorkflowExecution, Company)
            .join(WorkflowExecution, RiskAssessment.workflow_execution_id == WorkflowExecution.id)
            .join(LoanApplication, RiskAssessment.loan_application_id == LoanApplication.id)
            .join(Company, LoanApplication.company_id == Company.id)
            .order_by(RiskAssessment.created_at.desc())
            .limit(6)
        )
    ).all()
    recent_decisions = [
        RecentDecision(
            case_id=a.loan_application_id,
            company_legal_name=company.legal_name,
            run_id=execution.thread_id or str(execution.id),
            grade=a.grade,
            decision=a.decision.value if a.decision else None,
            final_score=a.final_score,
            finished_at=execution.finished_at,
        )
        for a, execution, company in recent
        if a.loan_application_id is not None
    ]

    return DashboardSummary(
        active_cases=active_cases,
        awaiting_review=status_counts.get(WorkflowExecutionStatus.PAUSED_HITL.value, 0),
        completed_runs=status_counts.get(WorkflowExecutionStatus.COMPLETED.value, 0),
        running=status_counts.get(WorkflowExecutionStatus.RUNNING.value, 0),
        failed=status_counts.get(WorkflowExecutionStatus.FAILED.value, 0),
        recent_decisions=recent_decisions,
        grade_distribution=grade_distribution,
        decision_distribution=decision_distribution,
    )

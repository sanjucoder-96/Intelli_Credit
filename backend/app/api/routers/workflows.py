"""Workflow run + HITL endpoints (Phase 5).

Runs are owned by the Celery worker over the durable checkpointer, so these
endpoints are thin: ``POST /cases/{id}/runs`` enqueues a run and returns its
id; ``GET /runs/{id}`` reports lifecycle + the deterministic grade/decision;
``POST /runs/{id}/hitl`` records the officer's bounded qualitative notches and
enqueues the resume; ``GET /runs/{id}/report`` returns the CAM. Reads ride the
RLS session, so a run from another org is simply not found (ADR-0013).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.providers import (
    get_tenant_context,
    get_tenant_db_session,
    require_permissions,
)
from app.api.schemas.workflows import (
    HitlSubmitRequest,
    RunReportResponse,
    RunStatusResponse,
    StartRunRequest,
    StartRunResponse,
)
from app.core.context import TenantContext
from app.core.errors import NotFoundError, ValidationFailedError
from app.domain.enums import Permission, WorkflowExecutionStatus
from app.infrastructure.db.models import (
    LoanApplication,
    Report,
    RiskAssessment,
    Workflow,
    WorkflowExecution,
)
from app.infrastructure.db.repositories.audit import record_audit
from app.tasks.workflows import enqueue_workflow_resume, enqueue_workflow_start

router = APIRouter(tags=["workflows"])


async def _load_execution(session: AsyncSession, run_id: str) -> WorkflowExecution:
    execution = (
        await session.execute(
            sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
        )
    ).scalar_one_or_none()
    if execution is None:
        raise NotFoundError(f"No workflow run '{run_id}'")
    return execution


async def _workflow_key(session: AsyncSession, execution: WorkflowExecution) -> str:
    workflow = await session.get(Workflow, execution.workflow_id)
    return workflow.key if workflow else ""


@router.post(
    "/cases/{case_id}/runs",
    response_model=StartRunResponse,
    status_code=202,
    dependencies=[Depends(require_permissions(Permission.RUN_START))],
)
async def start_run(
    case_id: UUID,
    body: StartRunRequest,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> StartRunResponse:
    """Enqueue a workflow run for a case; the worker owns execution."""
    case = await session.get(LoanApplication, case_id)
    if case is None:
        raise NotFoundError(f"No case '{case_id}'")

    scratch = dict(body.scratch or {})
    if body.financials:
        scratch["financials"] = body.financials

    run_id = str(uuid4())
    enqueue_workflow_start(
        run_id=run_id,
        workflow_key=body.workflow_key,
        version=body.version,
        org_id=str(ctx.org_id),
        case_id=str(case.id),
        scratch=scratch,
        created_by=str(ctx.user_id),
    )
    await record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="workflow.start",
        entity_type="workflow_execution",
        entity_id=case.id,
        payload={"run_id": run_id, "workflow_key": body.workflow_key},
    )
    return StartRunResponse(run_id=run_id, workflow_key=body.workflow_key)


@router.get(
    "/runs/{run_id}",
    response_model=RunStatusResponse,
    dependencies=[Depends(require_permissions(Permission.REPORT_READ))],
)
async def get_run(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> RunStatusResponse:
    """Lifecycle status + the deterministic grade/decision, when available."""
    execution = await _load_execution(session, run_id)
    assessment = (
        await session.execute(
            sa.select(RiskAssessment).where(RiskAssessment.workflow_execution_id == execution.id)
        )
    ).scalar_one_or_none()
    report = (
        await session.execute(sa.select(Report).where(Report.workflow_execution_id == execution.id))
    ).scalar_one_or_none()
    return RunStatusResponse(
        run_id=run_id,
        workflow_key=await _workflow_key(session, execution),
        status=execution.status,
        cost_usd=float(execution.cost_usd),
        grade=assessment.grade if assessment else None,
        decision=assessment.decision.value if assessment and assessment.decision else None,
        report_id=report.id if report else None,
        error=execution.error,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
    )


@router.post(
    "/runs/{run_id}/hitl",
    response_model=StartRunResponse,
    status_code=202,
    dependencies=[Depends(require_permissions(Permission.HITL_REVIEW))],
)
async def submit_hitl(
    run_id: str,
    body: HitlSubmitRequest,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> StartRunResponse:
    """Record the officer's qualitative notches and enqueue the resume."""
    execution = await _load_execution(session, run_id)
    if execution.status is not WorkflowExecutionStatus.PAUSED_HITL:
        raise ValidationFailedError(
            "Run is not awaiting human review",
            details={"status": execution.status.value},
        )
    resume_state = {
        "scratch": {
            "officer_notes": [note.model_dump() for note in body.notes],
            "knockouts": body.knockouts,
        }
    }
    enqueue_workflow_resume(run_id=run_id, resume_state=resume_state)
    await record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="workflow.hitl_submit",
        entity_type="workflow_execution",
        entity_id=execution.id,
        payload={"run_id": run_id, "note_count": len(body.notes)},
    )
    return StartRunResponse(
        run_id=run_id, workflow_key=await _workflow_key(session, execution), status="resuming"
    )


@router.get(
    "/runs/{run_id}/report",
    response_model=RunReportResponse,
    dependencies=[Depends(require_permissions(Permission.REPORT_READ))],
)
async def get_report(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> RunReportResponse:
    """The rendered CAM for a completed run."""
    execution = await _load_execution(session, run_id)
    report = (
        await session.execute(sa.select(Report).where(Report.workflow_execution_id == execution.id))
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError(f"No report for run '{run_id}' yet")
    ir = report.ir or {}
    return RunReportResponse(
        report_id=report.id,
        report_type=report.report_type,
        grade=ir.get("grade"),
        decision=ir.get("decision"),
        markdown=(report.artifacts or {}).get("markdown", ""),
        ir=ir,
    )

"""Workflow router logic (Phase 5): case lookup + enqueue on start, status
assembly from the deterministic RiskAssessment/Report, the HITL guard + resume
payload shape, and CAM retrieval. Handlers are invoked directly over a real DB
session (auth/DI wiring is covered by the auth + permission tests); the Celery
enqueue is recorded, not executed.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa

from app.api.routers import workflows as wf
from app.api.schemas.workflows import HitlNote, HitlSubmitRequest, StartRunRequest
from app.core.context import TenantContext
from app.core.errors import ValidationFailedError
from app.domain.enums import (
    DataOrigin,
    Decision,
    ReportStatus,
    Role,
    WorkflowExecutionStatus,
)
from app.infrastructure.db.models import (
    Company,
    LoanApplication,
    Organization,
    Report,
    RiskAssessment,
    User,
    Workflow,
    WorkflowExecution,
)
from tests.support import admin_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]


class _Recorder:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def __call__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


async def _make_user(session: Any, org_id: uuid.UUID, role: Role) -> uuid.UUID:
    user = User(
        org_id=org_id,
        email=f"{role.value}-{uuid.uuid4().hex[:8]}@example.com",
        full_name=f"{role.value} user",
        hashed_password="x",
        role=role,
    )
    session.add(user)
    await session.flush()
    return user.id


async def _seed(session: Any) -> tuple[TenantContext, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"API {suffix}", slug=f"api-{suffix}", is_active=True)
    session.add(org)
    await session.flush()
    company = Company(
        org_id=org.id, legal_name=f"Acme {suffix}", data_origin=DataOrigin.SYNTHETIC_DEMO
    )
    session.add(company)
    await session.flush()
    case = LoanApplication(org_id=org.id, company_id=company.id, title=f"case {suffix}")
    session.add(case)
    user_id = await _make_user(session, org.id, Role.ANALYST)
    await session.commit()
    ctx = TenantContext(org_id=org.id, user_id=user_id, role=Role.ANALYST)
    return ctx, case.id


async def _seed_paused_run(session: Any, ctx: TenantContext, case_id: uuid.UUID) -> str:
    run_id = str(uuid.uuid4())
    workflow = (
        await session.execute(
            sa.select(Workflow).where(Workflow.key == "credit_appraisal", Workflow.version == 1)
        )
    ).scalar_one_or_none()
    if workflow is None:
        workflow = Workflow(
            key="credit_appraisal",
            version=1,
            definition={},
            checksum=uuid.uuid4().hex,
            is_active=True,
        )
        session.add(workflow)
        await session.flush()
    execution = WorkflowExecution(
        org_id=ctx.org_id,
        workflow_id=workflow.id,
        loan_application_id=case_id,
        thread_id=run_id,
        status=WorkflowExecutionStatus.PAUSED_HITL,
        cost_usd=Decimal("0.0123"),
    )
    session.add(execution)
    await session.flush()
    session.add(
        RiskAssessment(
            org_id=ctx.org_id,
            workflow_execution_id=execution.id,
            scorecard_key="credit_appraisal",
            scorecard_version="1",
            grade="IC3",
            decision=Decision.APPROVE,
            final_score=74.0,
            coverage=0.8,
        )
    )
    session.add(
        Report(
            org_id=ctx.org_id,
            workflow_execution_id=execution.id,
            report_type="cam",
            template_key="cam_markdown_v1",
            ir={"grade": "IC3", "decision": "approve"},
            artifacts={"markdown": "# Credit Appraisal Memo — Acme"},
            status=ReportStatus.RENDERED,
        )
    )
    await session.commit()
    return run_id


async def test_start_run_enqueues_with_financials(monkeypatch: Any) -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            ctx, case_id = await _seed(session)
            rec = _Recorder()
            monkeypatch.setattr(wf, "enqueue_workflow_start", rec)
            resp = await wf.start_run(
                case_id,
                StartRunRequest(financials={"revenue": "100"}),
                ctx,
                session,
            )
        assert resp.workflow_key == "credit_appraisal"
        assert resp.status == "queued"
        assert rec.kwargs is not None
        assert rec.kwargs["org_id"] == str(ctx.org_id)
        assert rec.kwargs["case_id"] == str(case_id)
        assert rec.kwargs["scratch"]["financials"] == {"revenue": "100"}
        assert rec.kwargs["run_id"] == resp.run_id


async def test_get_run_reports_grade_and_report() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            ctx, case_id = await _seed(session)
            run_id = await _seed_paused_run(session, ctx, case_id)
            status = await wf.get_run(run_id, session)
        assert status.status is WorkflowExecutionStatus.PAUSED_HITL
        assert status.workflow_key == "credit_appraisal"
        assert status.grade == "IC3"
        assert status.decision == "approve"
        assert status.report_id is not None
        assert status.cost_usd == pytest.approx(0.0123)


async def test_submit_hitl_guards_and_enqueues_resume(monkeypatch: Any) -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            ctx, case_id = await _seed(session)
            run_id = await _seed_paused_run(session, ctx, case_id)
            officer_id = await _make_user(session, ctx.org_id, Role.CREDIT_OFFICER)
            await session.commit()
            officer = TenantContext(org_id=ctx.org_id, user_id=officer_id, role=Role.CREDIT_OFFICER)
            rec = _Recorder()
            monkeypatch.setattr(wf, "enqueue_workflow_resume", rec)
            resp = await wf.submit_hitl(
                run_id,
                HitlSubmitRequest(notes=[HitlNote(label="factory at 40% capacity", notch=-6.0)]),
                officer,
                session,
            )
        assert resp.status == "resuming"
        assert rec.kwargs is not None
        notes = rec.kwargs["resume_state"]["scratch"]["officer_notes"]
        assert notes == [{"label": "factory at 40% capacity", "notch": -6.0}]


async def test_submit_hitl_rejects_non_paused_run() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            ctx, case_id = await _seed(session)
            run_id = await _seed_paused_run(session, ctx, case_id)
            # advance the run out of the paused state
            execution = await wf._load_execution(session, run_id)
            execution.status = WorkflowExecutionStatus.COMPLETED
            await session.commit()
            with pytest.raises(ValidationFailedError):
                await wf.submit_hitl(run_id, HitlSubmitRequest(), ctx, session)


async def test_get_report_returns_markdown_and_ir() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            ctx, case_id = await _seed(session)
            run_id = await _seed_paused_run(session, ctx, case_id)
            report = await wf.get_report(run_id, session)
        assert report.report_type == "cam"
        assert report.grade == "IC3"
        assert "Credit Appraisal Memo" in report.markdown
        assert report.ir["decision"] == "approve"

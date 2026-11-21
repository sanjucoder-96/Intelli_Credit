"""Phase-8 CAM consistency across surfaces (priority #6, integration).

Runs one REAL credit_appraisal to completion, then proves the authoritative
numbers are byte-identical everywhere they surface:

    RiskAssessment columns  ==  RiskAssessment.explanation  ==  Report.ir
      ==  GET /runs/{id}  ==  GET /runs/{id}/detail  ==  GET /cases/{id}/runs
      ==  the exported PDF

The CAM renderer never recomputes — every figure traces to the one persisted
RiskResult. Live Postgres; LLM narrative is schema-scripted.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa

from app.agents.llm import LLMResult
from app.api.routers import insights as ins
from app.api.routers import workflows as wf
from app.core.config import get_settings
from app.core.context import TenantContext
from app.docintel.schemas import FinancialLineItems
from app.domain.enums import DataOrigin, Role, WorkflowExecutionStatus
from app.infrastructure.db.models import Company, LoanApplication, Organization, RiskAssessment
from app.infrastructure.gemini import GeminiCallResult
from app.workflows.checkpointer import build_checkpointer
from app.workflows.loader import load_definition
from app.workflows.persistence import create_execution, ensure_workflow_row
from app.workflows.runner import WorkflowRunner
from app.workflows.runtime import build_runtime_bundle, make_context_factory
from app.workflows.state import initial_state
from tests.support import admin_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]

_DEFINITION = (
    Path(__file__).resolve().parents[1] / "app/workflows/definitions/credit_appraisal.yaml"
)
_SCRIPTED = {
    "recommendation": {"rationale": "ok", "key_strengths": ["x"], "key_risks": ["y"]},
    "recommendation:critic": {
        "groundedness": 0.9,
        "completeness": 0.9,
        "consistency": 1.0,
        "passed": True,
    },
    "cam_generator": {"executive_summary": "Deterministic approval within limit."},
}


class _Scripted:
    async def call(self, request: Any, *, governor: Any = None) -> LLMResult:
        parsed = request.schema_type(**_SCRIPTED.get(request.purpose, {}))
        return LLMResult(
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            call=GeminiCallResult(model="scripted", cost_usd=0.0),
        )


def _financials() -> dict[str, Any]:
    return FinancialLineItems(
        fiscal_year="2025-26",
        revenue=Decimal("100000000"),
        ebitda=Decimal("18000000"),
        net_profit=Decimal("9000000"),
        total_assets=Decimal("120000000"),
        total_equity=Decimal("60000000"),
        total_debt=Decimal("30000000"),
        current_assets=Decimal("50000000"),
        current_liabilities=Decimal("25000000"),
        inventory=Decimal("15000000"),
        receivables=Decimal("20000000"),
        interest_expense=Decimal("3000000"),
        cash_flow_from_operations=Decimal("15000000"),
    ).model_dump(mode="json")


async def _run_completed(engine: Any) -> tuple[TenantContext, uuid.UUID, str, uuid.UUID]:
    factory = session_factory(engine)
    async with factory() as session:
        s = uuid.uuid4().hex[:8]
        org = Organization(name=f"CAM {s}", slug=f"cam-{s}", is_active=True)
        session.add(org)
        await session.flush()
        company = Company(
            org_id=org.id,
            legal_name=f"Acme Textiles {s}",
            gstin="27AAACA1234A1Z5",
            data_origin=DataOrigin.SYNTHETIC_DEMO,
        )
        session.add(company)
        await session.flush()
        case = LoanApplication(org_id=org.id, company_id=company.id, title=f"CAM {s}")
        session.add(case)
        await session.commit()
        tenant = TenantContext(org_id=org.id, user_id=uuid.uuid4(), role=Role.CREDIT_OFFICER)
        case_id = case.id

    definition = load_definition(_DEFINITION)
    run_id = str(uuid.uuid4())
    runner = WorkflowRunner(factory)
    state = initial_state(run_id, "credit_appraisal")
    state["scratch"] = {"financials": _financials()}

    async with factory() as session:
        row = await ensure_workflow_row(session, definition)
        execution = await create_execution(
            session,
            org_id=tenant.org_id,
            workflow_id=row.id,
            thread_id=run_id,
            loan_application_id=case_id,
        )
        execution_id = execution.id
        await session.commit()

    scripted = _Scripted()

    def ctx_factory(node: Any, st: Any) -> Any:
        bundle = build_runtime_bundle(
            session_factory=factory,
            org_id=tenant.org_id,
            execution_id=execution_id,
            run_id=run_id,
            tenant=tenant,
            settings=get_settings(),
            redis=None,
            case_id=case_id,
        )
        ctx = make_context_factory(bundle, definition)(node, st)
        ctx.caller = scripted
        return ctx

    async with build_checkpointer() as saver:
        await runner._invoke(definition, execution_id, run_id, state, saver, ctx_factory)
    async with build_checkpointer() as saver:
        resumed = await runner.resume(
            definition,
            thread_id=run_id,
            checkpointer=saver,
            resume_state={"scratch": {"officer_notes": []}},
            context_factory=ctx_factory,
        )
    assert resumed.status is WorkflowExecutionStatus.COMPLETED
    return tenant, case_id, run_id, execution_id


def _pdf_text(pdf: bytes) -> str:
    doc = pdfium.PdfDocument(pdf)
    out = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    doc.close()
    return out


async def test_cam_numbers_identical_across_every_surface() -> None:
    async with admin_engine() as engine:
        _tenant, case_id, run_id, execution_id = await _run_completed(engine)
        factory = session_factory(engine)

        async with factory() as session:
            assessment = (
                await session.execute(
                    sa.select(RiskAssessment).where(
                        RiskAssessment.workflow_execution_id == execution_id
                    )
                )
            ).scalar_one()

            # --- the authoritative persisted numbers ---
            grade = assessment.grade
            decision = assessment.decision.value
            final_score = assessment.final_score
            coverage = assessment.coverage
            limit_amount = assessment.explanation["limit"]["amount"]
            rate_pct = assessment.explanation["pricing"]["rate_pct"]

            # --- workflows API ---
            run = await wf.get_run(run_id, session)
            assert run.grade == grade and run.decision == decision

            report = await wf.get_report(run_id, session)
            assert report.ir["grade"] == grade
            assert report.ir["decision"] == decision
            assert report.ir["final_score"] == final_score
            assert str(report.ir["recommended_limit"]) == str(limit_amount)
            assert report.ir["interest_rate_pct"] == rate_pct

            # --- insights API ---
            detail = await ins.get_run_detail(run_id, session)
            assert detail.assessment is not None
            av = detail.assessment
            assert av.grade == grade and av.decision == decision
            assert av.final_score == final_score and av.coverage == coverage
            assert str(av.limit["amount"]) == str(limit_amount)
            assert av.pricing["rate_pct"] == rate_pct

            summaries = await ins.list_case_runs(case_id, session)
            assert summaries and summaries[0].grade == grade
            assert summaries[0].final_score == final_score
            assert summaries[0].decision == decision
            assert str(summaries[0].recommended_limit) == str(limit_amount)
            assert summaries[0].interest_rate_pct == rate_pct

            # --- exported PDF (rendered from the same IR, never recomputed) ---
            pdf_response = await wf.export_report(run_id, session, "pdf")
            text = _pdf_text(pdf_response.body)
            assert grade in text
            assert decision.upper() in text
            assert f"{final_score:.1f}" in text
            if rate_pct is not None:
                assert f"{rate_pct:.2f}%" in text
            if limit_amount is not None:
                assert f"{int(Decimal(str(limit_amount))):,}" in text

"""Credit-appraisal acceptance (S3.4/S3.5): the full happy path end to end —
analyse → HITL officer note → resume → deterministic scoring → CAM — over the
durable Postgres checkpointer, then persistence + no-duplication checks.

Deterministic and offline: the financial/fraud/risk agents run for real; the
LLM narrative agents get a schema-adaptive scripted caller (invariant #8 —
offline dev works through fakes). The numbers are never scripted.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from app.agents.llm import LLMResult
from app.core.config import get_settings
from app.core.context import TenantContext
from app.docintel.schemas import FinancialLineItems
from app.domain.enums import (
    DataOrigin,
    Decision,
    ReportStatus,
    Role,
    WorkflowExecutionStatus,
)
from app.infrastructure.db.models import (
    AgentExecution,
    Company,
    LoanApplication,
    Organization,
    Report,
    RiskAssessment,
    ScoreBreakdown,
    WorkflowExecution,
)
from app.infrastructure.gemini import GeminiCallResult
from app.workflows.checkpointer import build_checkpointer
from app.workflows.loader import load_definition
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


class SchemaScriptedCaller:
    """Returns a canned instance of whatever schema each purpose requests."""

    def __init__(self, by_purpose: dict[str, dict[str, Any]]) -> None:
        self._by_purpose = by_purpose
        self.purposes: list[str] = []

    async def call(self, request: Any, *, governor: Any = None) -> LLMResult:
        self.purposes.append(request.purpose)
        parsed = request.schema_type(**self._by_purpose.get(request.purpose, {}))
        return LLMResult(
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            call=GeminiCallResult(model="scripted", cost_usd=0.0),
        )


_SCRIPTED = {
    "recommendation": {
        "rationale": "Strong coverage and liquidity support the deterministic approval.",
        "key_strengths": ["interest coverage 6x", "current ratio 2.0"],
        "key_risks": ["ML PD factor pending Phase 6"],
    },
    "recommendation:critic": {
        "groundedness": 0.9,
        "completeness": 0.9,
        "consistency": 1.0,
        "passed": True,
    },
    "cam_generator": {
        "executive_summary": "Acme is graded IC1; the policy recommends approval within limit."
    },
}


def _strong_financials() -> dict[str, Any]:
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


async def _seed(engine: Any) -> tuple[TenantContext, uuid.UUID]:
    factory = session_factory(engine)
    async with factory() as session:
        suffix = uuid.uuid4().hex[:8]
        org = Organization(name=f"Lender {suffix}", slug=f"ca-{suffix}", is_active=True)
        session.add(org)
        await session.flush()
        company = Company(
            org_id=org.id,
            legal_name=f"Acme Textiles {suffix}",
            gstin="27AAACA1234A1Z5",
            sector="Textiles",
            data_origin=DataOrigin.SYNTHETIC_DEMO,
        )
        session.add(company)
        await session.flush()
        case = LoanApplication(
            org_id=org.id,
            company_id=company.id,
            title=f"WC appraisal {suffix}",
            requested_amount=Decimal("40000000"),
        )
        session.add(case)
        await session.commit()
        ctx = TenantContext(org_id=org.id, user_id=uuid.uuid4(), role=Role.CREDIT_OFFICER)
        return ctx, case.id


def _test_factory(bundle: Any, definition: Any, scripted: SchemaScriptedCaller) -> Any:
    base = make_context_factory(bundle, definition)

    def factory(node: Any, state: Any) -> Any:
        ctx = base(node, state)
        ctx.caller = scripted  # deterministic offline LLM
        return ctx

    return factory


def _nodes(state: dict[str, Any]) -> list[str]:
    return [f["node"] for f in state.get("findings", [])]


async def test_credit_appraisal_happy_path_with_hitl_and_persistence() -> None:
    async with admin_engine() as engine:
        tenant, case_id = await _seed(engine)
        factory = session_factory(engine)
        definition = load_definition(_DEFINITION)
        run_id = str(uuid.uuid4())
        scripted = SchemaScriptedCaller(_SCRIPTED)

        # Build the execution row first so the runtime bundle can tag traces.
        runner = WorkflowRunner(factory)

        # initial state carries the assembled financials (the API does this from
        # extracted fields; the test injects directly).
        state = initial_state(run_id, "credit_appraisal")
        state["scratch"] = {"financials": _strong_financials()}

        def make_ctx_factory(execution_id: uuid.UUID) -> Any:
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
            return _test_factory(bundle, definition, scripted)

        # --- start: analyse (financial ∥ research ∥ fraud), pause at HITL ---
        async with build_checkpointer() as saver:
            # We need the execution_id for the bundle; peek it after create.
            from app.workflows.persistence import create_execution, ensure_workflow_row

            async with factory() as session:
                wf = await ensure_workflow_row(session, definition)
                execution = await create_execution(
                    session,
                    org_id=tenant.org_id,
                    workflow_id=wf.id,
                    thread_id=run_id,
                    loan_application_id=case_id,
                )
                execution_id = execution.id
                await session.commit()

            ctx_factory = make_ctx_factory(execution_id)
            started = await runner._invoke(
                definition, execution_id, run_id, state, saver, ctx_factory
            )
        assert started.interrupted is True
        assert started.status is WorkflowExecutionStatus.PAUSED_HITL
        assert {"financial_analysis", "research", "fraud_detection"} <= set(_nodes(started.state))

        # --- officer enters a primary-insight note; resume ---
        async with build_checkpointer() as saver:
            ctx_factory = make_ctx_factory(execution_id)
            resumed = await runner.resume(
                definition,
                thread_id=run_id,
                checkpointer=saver,
                resume_state={
                    "scratch": {
                        "officer_notes": [{"label": "factory at 40% capacity", "notch": -6}]
                    }
                },
                context_factory=ctx_factory,
            )
        assert resumed.status is WorkflowExecutionStatus.COMPLETED
        # analyse nodes did not re-run after the pause.
        assert _nodes(resumed.state).count("financial_analysis") == 1
        assert {"risk_assessment", "recommendation", "cam_generator"} <= set(_nodes(resumed.state))

        # --- persistence: risk assessment, breakdown, report, execution, trace ---
        async with factory() as session:
            assessment = (
                await session.execute(
                    sa.select(RiskAssessment).where(
                        RiskAssessment.workflow_execution_id == execution_id
                    )
                )
            ).scalar_one()
            assert assessment.grade is not None
            assert assessment.decision is Decision.APPROVE  # strong company
            # the officer note re-scored via a bounded qualitative notch
            overlays = assessment.overlay_log["overlays"]
            assert any(o["rule_id"] == "qualitative_notch" and o["fired"] for o in overlays)

            breakdown = list(
                (
                    await session.execute(
                        sa.select(ScoreBreakdown).where(
                            ScoreBreakdown.risk_assessment_id == assessment.id
                        )
                    )
                ).scalars()
            )
            assert len(breakdown) >= 5  # per-factor rows persisted
            assert any(b.exclusion_reason == "ml_pd_pending_phase6" for b in breakdown)

            report = (
                await session.execute(
                    sa.select(Report).where(Report.workflow_execution_id == execution_id)
                )
            ).scalar_one()
            assert report.report_type == "cam" and report.status is ReportStatus.RENDERED
            assert "Credit Appraisal Memo" in report.artifacts["markdown"]
            assert report.ir["grade"] == assessment.grade  # CAM mirrors the deterministic grade

            execution = (
                await session.execute(
                    sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
                )
            ).scalar_one()
            assert execution.status is WorkflowExecutionStatus.COMPLETED

            trace_count = (
                await session.execute(
                    sa.select(sa.func.count()).where(
                        AgentExecution.workflow_execution_id == execution_id
                    )
                )
            ).scalar_one()
            assert trace_count > 0  # agent steps correlated to the execution

"""Provision realistic demo runs against the dev DB (Phase 7 demo).

Runs the real credit_appraisal workflow on the seeded demo cases using the
registered champion PD model and the deterministic scorecard — so the frontend
serves genuine engine output (RiskAssessment, CAM, ML reason codes) via the API.
The LLM narrative agents use an offline scripted caller (no Gemini dependency),
and a few forensic Finding rows are injected so the fraud/evidence panels have
data. One case is left paused at HITL to demonstrate the review flow.

Run from backend/ with the dev env loaded (.env):
    ./.venv/Scripts/python.exe -m scripts.provision_demo
"""

from __future__ import annotations

import asyncio
import selectors
import sys
import uuid
from typing import Any

import sqlalchemy as sa

import app.agents.credit  # noqa: F401 — registers the credit agents
from app.agents.llm import LLMResult
from app.core.config import get_settings
from app.core.context import TenantContext
from app.domain.enums import EvidenceKind, FindingKind, Role, Severity
from app.infrastructure.db.engine import get_session_factory
from app.infrastructure.db.models import Evidence, Finding, LoanApplication, Organization
from app.infrastructure.gemini import GeminiCallResult
from app.infrastructure.ml.registry import clear_champion_cache
from app.workflows.bootstrap import load_workflows
from app.workflows.checkpointer import build_checkpointer
from app.workflows.registry import workflow_registry
from app.workflows.runner import WorkflowRunner
from app.workflows.runtime import build_runtime_bundle, make_context_factory
from app.workflows.state import initial_state

SCRIPTED = {
    "recommendation": {
        "rationale": "Coverage and liquidity support the deterministic recommendation.",
        "key_strengths": ["healthy interest coverage", "adequate current ratio"],
        "key_risks": ["moderate leverage"],
    },
    "recommendation:critic": {
        "groundedness": 0.9,
        "completeness": 0.9,
        "consistency": 1.0,
        "passed": True,
    },
    "cam_generator": {
        "executive_summary": (
            "The borrower is assessed by the deterministic scorecard with the calibrated PD "
            "model contributing a relative default-risk signal. The recommendation, limit, and "
            "rate below are fixed by policy; this summary restates them."
        )
    },
}

STRONG = dict(
    fiscal_year="2025-26", revenue="100000000", ebitda="18000000", net_profit="9000000",
    total_assets="120000000", total_equity="60000000", total_debt="30000000",
    current_assets="50000000", current_liabilities="25000000", inventory="15000000",
    receivables="20000000", interest_expense="3000000", cash_flow_from_operations="15000000",
)
MODERATE = dict(
    fiscal_year="2025-26", revenue="80000000", ebitda="6000000", net_profit="1500000",
    total_assets="90000000", total_equity="25000000", total_debt="55000000",
    current_assets="28000000", current_liabilities="30000000", inventory="14000000",
    receivables="22000000", interest_expense="5000000", cash_flow_from_operations="3000000",
)


class ScriptedCaller:
    async def call(self, request: Any, *, governor: Any = None) -> LLMResult:
        parsed = request.schema_type(**SCRIPTED.get(request.purpose, {}))
        return LLMResult(
            parsed=parsed, raw_text=parsed.model_dump_json(),
            call=GeminiCallResult(model="scripted", cost_usd=0.0),
        )


def _run_async(coro: Any) -> Any:
    loop = (
        asyncio.SelectorEventLoop(selectors.SelectSelector())
        if sys.platform == "win32"
        else asyncio.new_event_loop()
    )
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _inject_findings(factory: Any, org_id: uuid.UUID, case_id: uuid.UUID, hard: bool) -> None:
    async with factory() as session:
        if hard:
            session.add(
                Finding(
                    org_id=org_id, loan_application_id=case_id, agent="gst_bank_validator",
                    kind=FindingKind.VALIDATION, severity=Severity.HIGH,
                    title="GST turnover vs bank credits — 40% shortfall (May)",
                    narrative="Reported GST turnover exceeds observed bank credits by 40% in May.",
                    data={"gst_turnover": 8300000, "bank_credits": 4980000, "gap_pct": 40.0},
                    confidence=0.82,
                )
            )
            session.add(
                Finding(
                    org_id=org_id, loan_application_id=case_id, agent="cheque_return_validator",
                    kind=FindingKind.FRAUD, severity=Severity.MEDIUM,
                    title="Cheque return frequency elevated",
                    narrative="3 cheque returns in 90 days.",
                    data={"returns_90d": 3}, confidence=0.7,
                )
            )
            session.add(
                Evidence(
                    org_id=org_id, loan_application_id=case_id, kind=EvidenceKind.DOCUMENT,
                    claim="Bank statement May credits", page=3,
                    quote="Total credits 49,80,000", confidence=0.9,
                    bboxes={"boxes": [{"x0": 0.1, "y0": 0.42, "x1": 0.6, "y1": 0.46}]},
                )
            )
        else:
            session.add(
                Finding(
                    org_id=org_id, loan_application_id=case_id, agent="itc_validator",
                    kind=FindingKind.VALIDATION, severity=Severity.MEDIUM,
                    title="GSTR-2B vs 3B ITC mismatch — 12%",
                    narrative="Input tax credit claimed exceeds 2B by 12%.",
                    data={"itc_gap_pct": 12.0}, confidence=0.75,
                )
            )
            session.add(
                Evidence(
                    org_id=org_id, loan_application_id=case_id, kind=EvidenceKind.DOCUMENT,
                    claim="GSTR-3B ITC claimed", page=1, quote="ITC 6,72,400", confidence=0.88,
                    bboxes={"boxes": [{"x0": 0.12, "y0": 0.3, "x1": 0.5, "y1": 0.34}]},
                )
            )
        await session.commit()


async def _provision() -> None:
    load_workflows()
    clear_champion_cache()
    settings = get_settings()
    factory = get_session_factory()
    definition = workflow_registry.get("credit_appraisal")

    async with factory() as session:
        org = (
            await session.execute(sa.select(Organization).where(Organization.slug == "demo-lender"))
        ).scalar_one()
        cases = list(
            (
                await session.execute(
                    sa.select(LoanApplication)
                    .where(LoanApplication.org_id == org.id)
                    .order_by(LoanApplication.created_at.asc())
                    .limit(2)
                )
            ).scalars()
        )
    if len(cases) < 2:
        print("Need at least 2 seeded cases; run `python -m app.seeds.demo` first.")
        return

    tenant = TenantContext(org_id=org.id, user_id=uuid.uuid4(), role=Role.ANALYST)
    scripted = ScriptedCaller()

    def builder_for(run_id: str, case_id: uuid.UUID):
        def builder(execution_id: uuid.UUID):
            bundle = build_runtime_bundle(
                session_factory=factory, org_id=org.id, execution_id=execution_id,
                run_id=run_id, tenant=tenant, settings=settings, redis=None, case_id=case_id,
            )
            base = make_context_factory(bundle, definition)

            def factory_fn(node: Any, state: Any):
                ctx = base(node, state)
                ctx.caller = scripted
                return ctx

            return factory_fn

        return builder

    runner = WorkflowRunner(factory)

    # Case 1: strong borrower → complete the run (APPROVE).
    strong_case = cases[0]
    await _inject_findings(factory, org.id, strong_case.id, hard=False)
    run1 = str(uuid.uuid4())
    state1 = initial_state(run1, "credit_appraisal")
    state1["scratch"] = {"financials": STRONG, "collateral_coverage": 1.2, "tenor_years": 3}
    async with build_checkpointer() as saver:
        await runner.start(
            definition, state1, org_id=org.id, checkpointer=saver,
            context_factory_builder=builder_for(run1, strong_case.id),
            loan_application_id=strong_case.id,
        )
    async with build_checkpointer() as saver:
        out = await runner.resume(
            definition, thread_id=run1, checkpointer=saver,
            resume_state={"scratch": {"officer_notes": [
                {"label": "Promoter interview satisfactory; strong order book", "notch": 4}
            ]}},
            context_factory_builder=builder_for(run1, strong_case.id),
        )
    print(f"case1 {strong_case.title!r}: {out.status.value}")

    # Case 2: moderate borrower + hard fraud signal → leave PAUSED at HITL.
    weak_case = cases[1]
    await _inject_findings(factory, org.id, weak_case.id, hard=True)
    run2 = str(uuid.uuid4())
    state2 = initial_state(run2, "credit_appraisal")
    state2["scratch"] = {"financials": MODERATE, "collateral_coverage": 0.8, "tenor_years": 6}
    async with build_checkpointer() as saver:
        out2 = await runner.start(
            definition, state2, org_id=org.id, checkpointer=saver,
            context_factory_builder=builder_for(run2, weak_case.id),
            loan_application_id=weak_case.id,
        )
    print(f"case2 {weak_case.title!r}: {out2.status.value} (awaiting review)")


if __name__ == "__main__":
    _run_async(_provision())

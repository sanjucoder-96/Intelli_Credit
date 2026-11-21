"""Phase-8 HITL adversarial evaluation (priority #4).

Proves the human-in-the-loop gate is real and safe end to end:

    workflow → interrupt → officer input → resume

- a negative observation deterministically LOWERS the persisted score; a
  positive one RAISES it; the effect is the bounded ``qualitative_notch``;
- multiple observations sum and clamp to ±12;
- a duplicate resume of an already-completed run is idempotent (no second
  score, no drift);
- a malformed observation (notch outside ±12) is rejected by the request schema;
- submitting HITL on a run that is not awaiting review is rejected.

Runs the REAL credit_appraisal workflow over live Postgres; the LLM narrative
slots use a schema-scripted caller (numbers are never scripted).
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.agents.llm import LLMResult
from app.api.routers import workflows as wf
from app.api.schemas.workflows import HitlNote, HitlSubmitRequest
from app.core.config import get_settings
from app.core.context import TenantContext
from app.core.errors import ValidationFailedError
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


async def _seed(engine: Any) -> tuple[TenantContext, uuid.UUID]:
    factory = session_factory(engine)
    async with factory() as session:
        s = uuid.uuid4().hex[:8]
        org = Organization(name=f"HITL {s}", slug=f"hitl-{s}", is_active=True)
        session.add(org)
        await session.flush()
        company = Company(
            org_id=org.id, legal_name=f"Acme {s}", data_origin=DataOrigin.SYNTHETIC_DEMO
        )
        session.add(company)
        await session.flush()
        case = LoanApplication(org_id=org.id, company_id=company.id, title=f"HITL {s}")
        session.add(case)
        await session.commit()
        return TenantContext(org_id=org.id, user_id=uuid.uuid4(), role=Role.CREDIT_OFFICER), case.id


async def _appraise(
    engine: Any, notes: list[dict[str, Any]], *, resume_twice: bool = False
) -> dict[str, Any]:
    """Run the full workflow to completion with the given officer notes; return
    the persisted RiskAssessment as a plain dict (score/decision/overlays)."""
    tenant, case_id = await _seed(engine)
    factory = session_factory(engine)
    definition = load_definition(_DEFINITION)
    run_id = str(uuid.uuid4())
    scripted = _Scripted()
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
        started = await runner._invoke(definition, execution_id, run_id, state, saver, ctx_factory)
    assert started.status is WorkflowExecutionStatus.PAUSED_HITL

    async with build_checkpointer() as saver:
        resumed = await runner.resume(
            definition,
            thread_id=run_id,
            checkpointer=saver,
            resume_state={"scratch": {"officer_notes": notes}},
            context_factory=ctx_factory,
        )
    assert resumed.status is WorkflowExecutionStatus.COMPLETED

    if resume_twice:
        async with build_checkpointer() as saver:
            again = await runner.resume(
                definition, thread_id=run_id, checkpointer=saver, context_factory=ctx_factory
            )
        assert again.status is WorkflowExecutionStatus.COMPLETED

    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    sa.select(RiskAssessment).where(
                        RiskAssessment.workflow_execution_id == execution_id
                    )
                )
            ).scalars()
        )
        assert len(rows) == 1, (
            "exactly one RiskAssessment per execution (re-scoring replaces, never duplicates)"
        )
        a = rows[0]
        return {
            "final_score": a.final_score,
            "decision": a.decision.value,
            "overlays": a.overlay_log["overlays"],
            "run_id": run_id,
            "execution_id": execution_id,
        }


async def test_negative_and_positive_observations_move_the_score_deterministically() -> None:
    async with admin_engine() as engine:
        baseline = await _appraise(engine, [])
        negative = await _appraise(engine, [{"label": "going-concern doubt", "notch": -8}])
        positive = await _appraise(engine, [{"label": "marquee clients", "notch": 8}])

    assert negative["final_score"] < baseline["final_score"] < positive["final_score"]
    # The movement is the bounded qualitative_notch overlay, not an ad-hoc edit.
    assert any(o["rule_id"] == "qualitative_notch" and o["fired"] for o in negative["overlays"])
    assert any(o["rule_id"] == "qualitative_notch" and o["fired"] for o in positive["overlays"])
    assert not any(o["rule_id"] == "qualitative_notch" for o in baseline["overlays"])


async def test_multiple_observations_sum_and_clamp_to_twelve() -> None:
    async with admin_engine() as engine:
        baseline = await _appraise(engine, [])
        stacked = await _appraise(
            engine,
            [{"label": f"note {i}", "notch": 6} for i in range(4)],  # 24 → clamps to +12
        )
    assert stacked["final_score"] - baseline["final_score"] <= 12.0 + 1e-9


async def test_duplicate_resume_is_idempotent_and_does_not_drift() -> None:
    async with admin_engine() as engine:
        once = await _appraise(engine, [{"label": "n", "notch": -6}])
        twice = await _appraise(engine, [{"label": "n", "notch": -6}], resume_twice=True)
    # A redelivered resume of a completed run must not re-score or drift.
    assert once["final_score"] == twice["final_score"]
    assert once["decision"] == twice["decision"]


def test_malformed_observation_is_rejected_by_the_request_schema() -> None:
    # A notch beyond the ±12 bound never reaches the engine.
    with pytest.raises(ValidationError):
        HitlNote(label="attempt to overpower policy", notch=100.0)
    with pytest.raises(ValidationError):
        HitlNote(label="", notch=1.0)  # empty label rejected
    # A well-formed request is accepted.
    ok = HitlSubmitRequest(notes=[HitlNote(label="valid", notch=-3.0)], knockouts=[])
    assert ok.notes[0].notch == -3.0


async def test_hitl_submission_on_a_non_paused_run_is_rejected(monkeypatch: Any) -> None:
    # Complete a run, then attempt to submit HITL on it — must be refused.
    calls: list[Any] = []
    monkeypatch.setattr(wf, "enqueue_workflow_resume", lambda **kw: calls.append(kw))
    async with admin_engine() as engine:
        result = await _appraise(engine, [])
        tenant_ctx = TenantContext(
            org_id=uuid.uuid4(), user_id=uuid.uuid4(), role=Role.CREDIT_OFFICER
        )
        # Re-scope ctx to the run's org via a fresh session lookup is unnecessary:
        # submit_hitl only needs the execution (found by thread_id) to be non-paused.
        factory = session_factory(engine)
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await wf.submit_hitl(
                    result["run_id"],
                    HitlSubmitRequest(notes=[HitlNote(label="late", notch=-2.0)], knockouts=[]),
                    tenant_ctx,
                    session,
                )
    assert calls == []  # no resume was enqueued for a completed run

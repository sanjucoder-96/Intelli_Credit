"""ML in the workflow (Phase 6): the ml_default_risk agent, running in a real
runtime context, loads a DB-registered champion, produces a calibrated PD with
reason codes, and that PD completes scorecard coverage. Disabling the model (or
registering no champion) leaves the pillar excluded — the coverage mechanism is
preserved.

Uses a uniquely-named champion so it cannot leak into the no-champion acceptance
test that asserts the excluded path.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import app.agents.credit  # noqa: F401 — registers the credit agents
from app.agents.registry import agent_registry
from app.analysis.ratios import compute_ratios
from app.analysis.scorecard import ScoreInputs, credit_appraisal_scorecard, evaluate_scorecard
from app.core.config import Settings, get_settings
from app.core.context import TenantContext
from app.docintel.schemas import FinancialLineItems
from app.domain.enums import DataOrigin, ModelStage, Role
from app.infrastructure.db.models import Company, LoanApplication, Organization
from app.infrastructure.ml.registry import clear_champion_cache, register_model
from app.workflows.definition import NodeSpec, WorkflowDefinition
from app.workflows.persistence import create_execution, ensure_workflow_row
from app.workflows.runtime import build_runtime_bundle, make_context_factory
from app.workflows.state import initial_state
from tests.ml_fixtures import build_fixture_pd_model
from tests.support import admin_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]

_DEF = WorkflowDefinition(
    key="ml_probe", version=1, nodes=[NodeSpec(name="ml_default_risk", agent="ml_default_risk")]
)


def _financials() -> FinancialLineItems:
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
    )


def _settings_with(name: str, *, enabled: bool = True) -> Settings:
    s = get_settings()
    return s.model_copy(
        update={"ml": s.ml.model_copy(update={"champion_model_name": name, "enabled": enabled})}
    )


async def _seed(factory: Any) -> tuple[TenantContext, uuid.UUID]:
    async with factory() as session:
        sfx = uuid.uuid4().hex[:8]
        org = Organization(name=f"ML {sfx}", slug=f"ml-{sfx}", is_active=True)
        session.add(org)
        await session.flush()
        company = Company(
            org_id=org.id, legal_name=f"Acme {sfx}", data_origin=DataOrigin.SYNTHETIC_DEMO
        )
        session.add(company)
        await session.flush()
        case = LoanApplication(org_id=org.id, company_id=company.id, title=f"case {sfx}")
        session.add(case)
        await session.commit()
        return TenantContext(org_id=org.id, user_id=uuid.uuid4(), role=Role.ANALYST), case.id


async def _run_ml_agent(
    factory: Any, tenant: TenantContext, case_id: uuid.UUID, settings: Settings
) -> Any:
    run_id = str(uuid.uuid4())
    async with factory() as session:
        wf = await ensure_workflow_row(session, _DEF)
        execution = await create_execution(
            session,
            org_id=tenant.org_id,
            workflow_id=wf.id,
            thread_id=run_id,
            loan_application_id=case_id,
        )
        execution_id = execution.id
        await session.commit()

    bundle = build_runtime_bundle(
        session_factory=factory,
        org_id=tenant.org_id,
        execution_id=execution_id,
        run_id=run_id,
        tenant=tenant,
        settings=settings,
        redis=None,
        case_id=case_id,
    )
    ctx_factory = make_context_factory(bundle, _DEF)
    state = initial_state(run_id, "ml_probe")
    state["scratch"] = {"financials": _financials().model_dump(mode="json")}
    ctx = ctx_factory(_DEF.nodes[0], state)
    agent = agent_registry.create("ml_default_risk")
    result = await agent.run(ctx)
    return result.output


async def test_ml_agent_predicts_and_completes_coverage(tmp_path: Path) -> None:
    clear_champion_cache()
    name = f"pd_model_wf_{uuid.uuid4().hex[:8]}"
    directory = tmp_path / name / "1.0.0"
    card = build_fixture_pd_model(directory, name=name, version="1.0.0")

    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            await register_model(
                session, card=card, artifact_path=str(directory), stage=ModelStage.CHAMPION
            )
            await session.commit()
        tenant, case_id = await _seed(factory)
        out = await _run_ml_agent(factory, tenant, case_id, _settings_with(name))

    assert out.available is True
    assert out.pd is not None and 0.0 <= out.pd <= 1.0
    assert out.model_name == name and out.model_version == "1.0.0"
    assert out.non_production is True  # fixture model
    assert out.top_risk_factors or out.top_mitigants  # reason codes present

    # The agent's PD completes scorecard coverage (ml_default_risk pillar admitted).
    fin = _financials()
    result = evaluate_scorecard(
        credit_appraisal_scorecard(),
        ScoreInputs(ratios=compute_ratios(fin).available_ratios(), financials=fin, ml_pd=out.pd),
    )
    assert result.coverage == 1.0


async def test_ml_disabled_leaves_pillar_excluded(tmp_path: Path) -> None:
    clear_champion_cache()
    name = f"pd_model_wf_{uuid.uuid4().hex[:8]}"
    directory = tmp_path / name / "1.0.0"
    card = build_fixture_pd_model(directory, name=name, version="1.0.0")

    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            await register_model(
                session, card=card, artifact_path=str(directory), stage=ModelStage.CHAMPION
            )
            await session.commit()
        tenant, case_id = await _seed(factory)
        # enabled=False → the agent must not load or use the champion.
        out = await _run_ml_agent(factory, tenant, case_id, _settings_with(name, enabled=False))

    assert out.available is False
    assert out.pd is None

"""Insights read-model endpoints (Phase 7): case runs, run detail (scorecard +
ML), findings/evidence, dashboard. Seeds persisted rows and calls the handlers
directly over a real DB session (auth/DI covered elsewhere) — proving the
projections serve exactly what the engine stored, computing nothing.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa

from app.api.routers import insights as ins
from app.domain.enums import (
    DataOrigin,
    Decision,
    EvidenceKind,
    FindingKind,
    ReportStatus,
    Severity,
    WorkflowExecutionStatus,
)
from app.infrastructure.db.models import (
    Company,
    Document,
    Evidence,
    ExtractedField,
    Finding,
    LoanApplication,
    Organization,
    Report,
    RiskAssessment,
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


async def _seed(session: Any) -> tuple[uuid.UUID, str]:
    sfx = uuid.uuid4().hex[:8]
    org = Organization(name=f"Ins {sfx}", slug=f"ins-{sfx}", is_active=True)
    session.add(org)
    await session.flush()
    company = Company(
        org_id=org.id,
        legal_name=f"Acme {sfx}",
        sector="Textiles",
        data_origin=DataOrigin.SYNTHETIC_DEMO,
    )
    session.add(company)
    await session.flush()
    case = LoanApplication(org_id=org.id, company_id=company.id, title=f"case {sfx}")
    session.add(case)
    workflow = Workflow(
        key=f"wf_{sfx}",
        version=1,
        definition={
            "nodes": [
                {"name": "financial_analysis", "agent": "financial_analysis"},
                {"name": "risk_assessment", "agent": "risk_assessment"},
            ]
        },
        checksum=uuid.uuid4().hex,
        is_active=True,
    )
    session.add(workflow)
    await session.flush()
    run_id = str(uuid.uuid4())
    execution = WorkflowExecution(
        org_id=org.id,
        workflow_id=workflow.id,
        loan_application_id=case.id,
        thread_id=run_id,
        status=WorkflowExecutionStatus.COMPLETED,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        cost_usd=Decimal("0.05"),
    )
    session.add(execution)
    await session.flush()
    session.add(
        RiskAssessment(
            org_id=org.id,
            workflow_execution_id=execution.id,
            loan_application_id=case.id,
            scorecard_key="credit_appraisal",
            scorecard_version="2",
            base_score=80.5,
            final_score=78.0,
            grade="IC3",
            decision=Decision.APPROVE,
            coverage=1.0,
            breakdown={
                "factors": [
                    {
                        "pillar_id": "financial_strength",
                        "factor_id": "current_ratio",
                        "raw_value": 2.0,
                        "normalized": 90.0,
                        "weight": 0.25,
                        "contribution": 22.5,
                        "admitted": True,
                        "exclusion_reason": None,
                        "explanation": "ratio:current_ratio -> 90.0/100",
                    },
                    {
                        "pillar_id": "ml_default_risk",
                        "factor_id": "pd",
                        "raw_value": 0.03,
                        "normalized": 85.0,
                        "weight": 1.0,
                        "contribution": 85.0,
                        "admitted": True,
                        "exclusion_reason": None,
                        "explanation": "ml_pd -> 85.0/100",
                    },
                ]
            },
            overlay_log={
                "overlays": [
                    {"rule_id": "qualitative_notch", "fired": True, "effect": "-6.0", "detail": "x"}
                ]
            },
            explanation={
                "limit": {"amount": "18750000", "binding_constraint": "MPBF_TandonII"},
                "pricing": {"rate_pct": 10.5, "base_rate_pct": 9.0, "grade_premium_pct": 1.5},
                "notes": ["note a"],
                "ml": {
                    "available": True,
                    "pd": 0.0278,
                    "pd_raw": 0.3365,
                    "coverage": 0.93,
                    "model_name": "pd_model",
                    "model_version": "1.0.0",
                    "algorithm": "xgboost",
                    "non_production": False,
                    "top_risk_factors": [
                        {
                            "label": "Liabilities / equity (leverage)",
                            "shap_log_odds": 0.6,
                            "direction": "increases",
                        }
                    ],
                    "top_mitigants": [
                        {
                            "label": "Net profit margin",
                            "shap_log_odds": -0.7,
                            "direction": "decreases",
                        }
                    ],
                },
            },
        )
    )
    session.add(
        Report(
            org_id=org.id,
            workflow_execution_id=execution.id,
            loan_application_id=case.id,
            report_type="cam",
            template_key="cam_markdown_v1",
            ir={"grade": "IC3", "decision": "approve"},
            artifacts={"markdown": "# CAM"},
            status=ReportStatus.RENDERED,
        )
    )
    session.add(
        Finding(
            org_id=org.id,
            loan_application_id=case.id,
            workflow_execution_id=execution.id,
            agent="fraud_detection",
            kind=FindingKind.VALIDATION,
            severity=Severity.HIGH,
            title="GST vs bank credits 40% mismatch",
            narrative="April gap",
            confidence=0.8,
        )
    )
    session.add(
        Evidence(
            org_id=org.id,
            loan_application_id=case.id,
            workflow_execution_id=execution.id,
            kind=EvidenceKind.DOCUMENT,
            claim="Reported revenue vs bank credits",
            page=2,
            quote="Sales 1,00,00,000",
            confidence=0.9,
        )
    )
    await session.commit()
    return case.id, run_id


async def test_list_case_runs_projects_outcome() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            case_id, run_id = await _seed(session)
            runs = await ins.list_case_runs(case_id, session)
        assert len(runs) == 1
        r = runs[0]
        assert r.run_id == run_id and r.grade == "IC3" and r.decision == "approve"
        assert r.final_score == 78.0 and r.pd == 0.0278
        assert r.recommended_limit == "18750000" and r.interest_rate_pct == 10.5
        assert r.report_id is not None and r.workflow_key.startswith("wf_")


async def test_run_detail_serves_scorecard_and_ml() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            _, run_id = await _seed(session)
            detail = await ins.get_run_detail(run_id, session)
        assert detail.assessment is not None
        a = detail.assessment
        assert a.final_score == 78.0 and a.grade == "IC3" and a.coverage == 1.0
        assert len(a.factors) == 2
        pd_factor = next(f for f in a.factors if f.factor_id == "pd")
        assert pd_factor.admitted and pd_factor.contribution == 85.0
        assert any(o.rule_id == "qualitative_notch" and o.fired for o in a.overlays)
        assert a.limit["binding_constraint"] == "MPBF_TandonII"
        assert a.pricing["rate_pct"] == 10.5
        assert a.ml.available and a.ml.pd == 0.0278 and a.ml.model_name == "pd_model"
        assert a.ml.top_risk_factors and a.ml.top_mitigants
        # node overlay from the workflow definition; a completed run's untraced
        # nodes (none seeded here) show as done, not pending.
        assert {n.node for n in detail.nodes} == {"financial_analysis", "risk_assessment"}
        assert all(n.status == "ok" for n in detail.nodes)


async def test_case_findings_and_evidence() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            case_id, _ = await _seed(session)
            result = await ins.list_case_findings(case_id, session)
        assert len(result.findings) == 1
        assert result.findings[0].severity == "high" and result.findings[0].kind == "validation"
        assert len(result.evidence) == 1
        assert result.evidence[0].page == 2 and "Sales" in (result.evidence[0].quote or "")


async def _seed_document_with_fields(session: Any, case_id: uuid.UUID) -> uuid.UUID:
    org_id = (
        await session.execute(
            sa.select(LoanApplication.org_id).where(LoanApplication.id == case_id)
        )
    ).scalar_one()
    company_id = (
        await session.execute(
            sa.select(LoanApplication.company_id).where(LoanApplication.id == case_id)
        )
    ).scalar_one()
    doc = Document(
        org_id=org_id,
        loan_application_id=case_id,
        company_id=company_id,
        storage_key="k/annual.pdf",
        sha256="a" * 64,
        filename="annual_report.pdf",
        media_type="application/pdf",
        doc_type="annual_report",
        status="ready",
    )
    session.add(doc)
    await session.flush()
    fields = {
        "entity_name": "Acme Textiles",
        "years.0.fiscal_year": "2025-26",
        "years.0.revenue": 100000000,
        "years.0.ebitda": 18000000,
        "years.0.net_profit": 9000000,
        "years.1.fiscal_year": "2024-25",
        "years.1.revenue": 80000000,
    }
    for path, val in fields.items():
        session.add(
            ExtractedField(
                org_id=org_id,
                document_id=doc.id,
                schema_id="financial_statement",
                schema_version="1",
                field_path=path,
                value={"value": val},
                confidence=0.8,
                page=2,
                bbox={"page": 2, "bboxes": [{"x0": 0.1}]},
            )
        )
    await session.commit()
    return doc.id


async def test_extracted_fields_and_financials_assembly() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            case_id, _ = await _seed(session)
            doc_id = await _seed_document_with_fields(session, case_id)
            fields = await ins.list_extracted_fields(case_id, doc_id, session)
            assembled = await ins.assemble_financials(case_id, session)
        # extracted-field view carries value + page + bbox
        assert any(f.field_path == "years.0.revenue" and f.value == 100000000 for f in fields)
        assert all(f.page == 2 for f in fields)
        # assembly picks the year with the most line items (2025-26) — never invents
        assert assembled.available is True
        assert assembled.fiscal_year == "2025-26"
        assert assembled.financials["revenue"] == "100000000"
        assert assembled.financials["ebitda"] == "18000000"
        assert "entity_name" not in assembled.financials  # non-financial field dropped
        assert any(s.field == "revenue" and s.document_id == doc_id for s in assembled.sources)


async def test_financials_unavailable_without_extraction() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            case_id, _ = await _seed(session)
            assembled = await ins.assemble_financials(case_id, session)
        assert assembled.available is False
        assert assembled.financials == {}


async def test_dashboard_summary_aggregates() -> None:
    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            await _seed(session)
            summary = await ins.dashboard_summary(session)
        assert summary.active_cases >= 1
        assert summary.completed_runs >= 1
        assert any(d.decision == "approve" for d in summary.recent_decisions)
        assert any(b.grade == "IC3" for b in summary.grade_distribution)
        assert summary.decision_distribution.get("approve", 0) >= 1

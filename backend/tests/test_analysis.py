"""Deterministic analysis: ratios, health score, scorecard, Five Cs, CAM IR.

Pure logic — no DB, no LLM. These are the numbers the platform stands behind
(invariant #3), so they get exhaustive deterministic tests.
"""

from __future__ import annotations

from decimal import Decimal

from app.analysis.cam import CompanyProfile, assemble_cam, render_markdown
from app.analysis.five_cs import derive_five_cs
from app.analysis.ratios import compute_ratios, financial_health_score
from app.analysis.scorecard import (
    QualitativeNote,
    ScoreInputs,
    credit_appraisal_scorecard,
    evaluate_scorecard,
)
from app.docintel.schemas import FinancialLineItems
from app.domain.enums import Decision


def _strong_financials() -> FinancialLineItems:
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


def _strong_inputs() -> ScoreInputs:
    fin = _strong_financials()
    ratios = compute_ratios(fin).available_ratios()
    return ScoreInputs(ratios=ratios, financials=fin, turnover=fin.revenue)


# ------------------------------------------------------------------- ratios
def test_ratios_compute_and_guard_missing() -> None:
    ratios = compute_ratios(_strong_financials())
    assert abs(ratios.current_ratio.value - 2.0) < 1e-6  # type: ignore[operator]
    assert abs(ratios.interest_coverage.value - 6.0) < 1e-6  # type: ignore[operator]
    assert abs(ratios.debt_to_equity.value - 0.5) < 1e-6  # type: ignore[operator]
    assert ratios.current_ratio.available and ratios.current_ratio.explanation

    sparse = compute_ratios(FinancialLineItems(revenue=Decimal("100")))
    assert sparse.current_ratio.value is None  # no CA/CL -> None, never fabricated
    assert sparse.current_ratio.available is False


def test_health_score_and_insufficient_data() -> None:
    score = financial_health_score(compute_ratios(_strong_financials()))
    assert score is not None and score > 80  # a strong company scores high
    assert financial_health_score(compute_ratios(FinancialLineItems())) is None


# ---------------------------------------------------------------- scorecard
def test_strong_company_is_approved_with_limit_and_rate() -> None:
    result = evaluate_scorecard(credit_appraisal_scorecard(), _strong_inputs())
    assert result.decision is Decision.APPROVE
    assert result.grade in {"IC1", "IC2"}
    assert 0.79 <= result.coverage <= 0.81  # ML pillar (0.20) honestly excluded
    assert result.limit.amount is not None and result.limit.binding_constraint is not None
    assert result.pricing.rate_pct is not None and result.pricing.rate_pct > 0
    # The ML-pending note is surfaced, not hidden.
    assert any("Phase 6" in note for note in result.notes)
    # No number came from an LLM — the whole result is arithmetic.
    assert result.base_score > 0 and result.final_score > 0


def test_critical_fraud_caps_and_declines() -> None:
    inputs = _strong_inputs()
    inputs.fraud_severity = "critical"
    result = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    assert result.final_score <= 45.0  # critical-fraud cap fired
    assert result.decision is Decision.DECLINE
    assert any(o.rule_id == "cap_critical_fraud" and o.fired for o in result.overlay_log)


def test_knockout_forces_decline() -> None:
    inputs = _strong_inputs()
    inputs.knockouts = ["wilful_defaulter"]
    result = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    assert result.decision is Decision.DECLINE
    assert any(o.rule_id == "knockout" and o.fired for o in result.overlay_log)
    assert result.limit.amount is None  # declined -> no limit


def test_missing_financials_low_coverage_refers() -> None:
    result = evaluate_scorecard(credit_appraisal_scorecard(), ScoreInputs())
    assert result.coverage < 0.7
    assert result.decision is Decision.REFER


def test_qualitative_notch_is_bounded() -> None:
    inputs = _strong_inputs()
    inputs.qualitative_notes = [
        QualitativeNote(label="factory at 40% capacity", notch=-20)  # exceeds clamp
    ]
    result = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    notch = next(o for o in result.overlay_log if o.rule_id == "qualitative_notch")
    assert notch.effect == "-12.0"  # clamped to -12, no single note swings a decision invisibly


# ------------------------------------------------------------------ five cs
def test_five_cs_derivation() -> None:
    inputs = _strong_inputs()
    ratios = compute_ratios(_strong_financials())
    result = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    five = derive_five_cs(result, ratios, inputs)
    assert five.capacity.score is not None
    assert five.capital.score is not None
    assert len(five.as_list()) == 5


# --------------------------------------------------------------------- cam
def test_cam_ir_numbers_come_from_the_risk_result() -> None:
    inputs = _strong_inputs()
    ratios = compute_ratios(_strong_financials())
    result = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    five = derive_five_cs(result, ratios, inputs)
    cam = assemble_cam(
        company=CompanyProfile(legal_name="Acme Textiles Pvt Ltd", gstin="27AAACA1234A1Z5"),
        risk=result,
        ratios=ratios,
        five_cs=five,
        findings=[],
        requested_amount=Decimal("40000000"),
        executive_summary="LLM narrative goes here.",
    )
    # CAM decision/grade/limit/rate mirror the deterministic result exactly.
    assert cam.decision == result.decision.value
    assert cam.grade == result.grade
    assert cam.recommended_limit == result.limit.amount
    assert cam.interest_rate_pct == result.pricing.rate_pct

    markdown = render_markdown(cam)
    assert "Credit Appraisal Memo" in markdown
    assert result.grade in markdown
    assert "Five Cs of Credit" in markdown
    assert "LLM narrative goes here." in markdown  # narrative slot rendered

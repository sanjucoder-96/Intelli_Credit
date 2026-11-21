"""Phase-5 ↔ Phase-6 integration at the scorecard boundary.

The PD model feeds the ml_default_risk pillar as a bounded input; it never sets a
score/limit/rate/decision. When a calibrated PD is present the pillar is admitted
and coverage rises to 100%; when absent (no champion / low coverage) it stays
excluded and coverage honestly drops — the Phase-5 coverage mechanism intact.
"""

from __future__ import annotations

from decimal import Decimal

from app.analysis.ratios import compute_ratios
from app.analysis.scorecard import (
    QualitativeNote,
    ScoreInputs,
    credit_appraisal_scorecard,
    evaluate_scorecard,
)
from app.docintel.schemas import FinancialLineItems


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


def _inputs(ml_pd: float | None) -> ScoreInputs:
    fin = _financials()
    return ScoreInputs(
        ratios=compute_ratios(fin).available_ratios(),
        financials=fin,
        turnover=fin.revenue,
        ml_pd=ml_pd,
    )


def test_ml_pd_admits_pillar_and_completes_coverage() -> None:
    result = evaluate_scorecard(credit_appraisal_scorecard(), _inputs(ml_pd=0.03))
    assert result.coverage == 1.0  # every pillar admitted now
    pd_factor = next(f for f in result.breakdown if f.factor_id == "pd")
    assert pd_factor.admitted is True
    assert pd_factor.raw_value == 0.03
    assert pd_factor.normalized is not None and pd_factor.normalized > 80  # low PD → strong score
    assert not any(f.exclusion_reason == "ml_default_risk_unavailable" for f in result.breakdown)


def test_absent_ml_pd_excludes_pillar_and_drops_coverage() -> None:
    result = evaluate_scorecard(credit_appraisal_scorecard(), _inputs(ml_pd=None))
    assert 0.79 <= result.coverage <= 0.81  # ML pillar (0.20) excluded
    pd_factor = next(f for f in result.breakdown if f.factor_id == "pd")
    assert pd_factor.admitted is False
    assert pd_factor.exclusion_reason == "ml_default_risk_unavailable"
    assert any("ML default-risk factor unavailable" in n for n in result.notes)


def test_high_pd_lowers_the_score_versus_low_pd() -> None:
    low = evaluate_scorecard(credit_appraisal_scorecard(), _inputs(ml_pd=0.02))
    high = evaluate_scorecard(credit_appraisal_scorecard(), _inputs(ml_pd=0.45))
    # Same financials; a high PD must push the final score strictly down.
    assert high.final_score < low.final_score
    pd_low = next(f for f in low.breakdown if f.factor_id == "pd").normalized
    pd_high = next(f for f in high.breakdown if f.factor_id == "pd").normalized
    assert pd_low is not None and pd_high is not None and pd_high < pd_low


def test_pd_never_overrides_the_decision_directly() -> None:
    # The PD contributes 20% of the score, but a knockout still forces DECLINE:
    # the ML signal cannot rescue or veto the deterministic policy.
    fin = _financials()
    inputs = ScoreInputs(
        ratios=compute_ratios(fin).available_ratios(),
        financials=fin,
        ml_pd=0.001,  # extremely low PD
        knockouts=["wilful_defaulter"],
    )
    result = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    assert result.decision.value == "decline"


def test_fraud_cap_is_a_hard_ceiling_the_notch_cannot_lift() -> None:
    # A strong applicant with a critical fraud signal is capped at 45; a +12
    # officer notch (or an excellent PD) must NOT lift the score back above it.
    fin = _financials()
    inputs = ScoreInputs(
        ratios=compute_ratios(fin).available_ratios(),
        financials=fin,
        ml_pd=0.01,  # excellent PD
        fraud_severity="critical",
        qualitative_notes=[QualitativeNote(label="strong management", notch=12.0)],
    )
    result = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    assert result.final_score <= 45.0
    assert result.decision.value == "decline"
    assert any(o.rule_id == "fraud_cap_reclamp" and o.fired for o in result.overlay_log)

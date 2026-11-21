"""Deterministic financial & decision analysis (ARCHITECTURE §9–§10).

Pure, side-effect-free logic that produces the platform's *numbers* — ratios,
scores, grades, limits, rates, the Five Cs, and the CAM intermediate
representation. The LLM narrates these numbers; it never invents them
(invariant #3). This package sits at the infrastructure/docintel layer so the
agents (risk_assessment, recommendation, cam_generator) consume it, and it
imports only the domain and docintel schemas below it.
"""

from app.analysis.ratios import FinancialRatios, compute_ratios, financial_health_score
from app.analysis.scorecard import (
    RiskResult,
    ScorecardConfig,
    credit_appraisal_scorecard,
    evaluate_scorecard,
)

__all__ = [
    "FinancialRatios",
    "RiskResult",
    "ScorecardConfig",
    "compute_ratios",
    "credit_appraisal_scorecard",
    "evaluate_scorecard",
    "financial_health_score",
]

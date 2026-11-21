"""Deterministic risk scorecard + policy grid (ADR-0009).

A weighted-factor scorecard with ordered, logged overlays and a deterministic
policy grid — the structure of a bank's internal rating model, audit-defensible
by construction. The LLM only classifies unstructured inputs into enumerated
levels (elsewhere); here every number is arithmetic over admitted evidence:

    S = Σ_pillar W_p · Σ_factor w_f · n_f(x_f)      (over admitted factors)

Missing factors are excluded and their weight redistributed within the pillar;
a fully-missing pillar (e.g. the ML default-risk pillar, which lands in Phase 6)
is excluded and *not* redistributed, so ``coverage`` honestly reflects it.
Grade → decision → limit → rate are pure functions of the breakdown, so the CAM
can print the exact arithmetic (invariant #1, #3).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.docintel.schemas import FinancialLineItems
from app.domain.enums import Decision

_SEVERITY_SCORE = {
    "none": 95.0,
    "info": 95.0,
    "low": 80.0,
    "medium": 60.0,
    "high": 40.0,
    "critical": 20.0,
}
_MISSING_ML_REASON = "ml_pd_pending_phase6"


class Normalizer(BaseModel):
    higher_is_better: bool = True
    breakpoints: list[tuple[float, float]]

    def apply(self, value: float) -> float:
        ordered = sorted(self.breakpoints, key=lambda p: p[0])
        if value <= ordered[0][0]:
            return ordered[0][1]
        if value >= ordered[-1][0]:
            return ordered[-1][1]
        for (x0, y0), (x1, y1) in zip(ordered, ordered[1:], strict=False):
            if x0 <= value <= x1:
                frac = (value - x0) / (x1 - x0) if x1 != x0 else 0.0
                return y0 + frac * (y1 - y0)
        return ordered[-1][1]


class FactorConfig(BaseModel):
    factor_id: str
    weight: float
    source: str  # "ratio:<name>" | "severity:fraud" | "severity:external" | "constant" | "ml_pd"
    normalizer: Normalizer | None = None
    constant: float | None = None


class PillarConfig(BaseModel):
    pillar_id: str
    weight: float
    factors: list[FactorConfig]


class QualitativeNote(BaseModel):
    """An officer note mapped to an enumerated level + a bounded notch."""

    label: str
    notch: float  # points; total clamped to ±12


class ScoreInputs(BaseModel):
    """Everything the scorecard consumes for one evaluation."""

    ratios: dict[str, float] = Field(default_factory=dict)
    fraud_severity: str | None = None
    external_severity: str | None = None
    qualitative_notes: list[QualitativeNote] = Field(default_factory=list)
    knockouts: list[str] = Field(default_factory=list)
    financials: FinancialLineItems | None = None
    turnover: Decimal | None = None
    collateral_coverage: float | None = None
    tenor_years: float | None = None


class FactorResult(BaseModel):
    pillar_id: str
    factor_id: str
    raw_value: float | None
    normalized: float | None
    weight: float
    contribution: float
    admitted: bool
    exclusion_reason: str | None = None
    explanation: str


class OverlayApplication(BaseModel):
    rule_id: str
    fired: bool
    effect: str
    detail: str = ""


class LimitResult(BaseModel):
    amount: Decimal | None = None
    binding_constraint: str | None = None
    arithmetic: str = ""


class PricingResult(BaseModel):
    rate_pct: float | None = None
    base_rate_pct: float = 0.0
    grade_premium_pct: float = 0.0
    adjustments: list[tuple[str, float]] = Field(default_factory=list)


class RiskResult(BaseModel):
    scorecard_key: str
    scorecard_version: str
    base_score: float
    final_score: float
    grade: str
    decision: Decision
    coverage: float
    breakdown: list[FactorResult]
    overlay_log: list[OverlayApplication]
    limit: LimitResult
    pricing: PricingResult
    notes: list[str] = Field(default_factory=list)


class ScorecardConfig(BaseModel):
    key: str
    version: str
    pillars: list[PillarConfig]
    base_rate_pct: float = 9.0
    ebitda_limit_multiple: float = 3.0
    turnover_limit_pct: float = 0.20
    mpbf_factor: float = 0.75
    grade_premium: dict[str, float] = Field(
        default_factory=lambda: {
            "IC1": 0.5,
            "IC2": 1.0,
            "IC3": 1.5,
            "IC4": 2.5,
            "IC5": 3.5,
            "IC6": 4.5,
        }
    )


# ---------------------------------------------------------------- grade bands
_GRADE_BANDS = [
    (85.0, "IC1"),
    (78.0, "IC2"),
    (71.0, "IC3"),
    (64.0, "IC4"),
    (57.0, "IC5"),
    (50.0, "IC6"),
    (43.0, "IC7"),
    (36.0, "IC8"),
    (29.0, "IC9"),
]


def _grade(score: float) -> str:
    for threshold, grade in _GRADE_BANDS:
        if score >= threshold:
            return grade
    return "IC10"


def _factor_raw(factor: FactorConfig, inputs: ScoreInputs) -> tuple[float | None, str | None]:
    """Resolve a factor's raw value; second element is an exclusion reason."""
    kind, _, arg = factor.source.partition(":")
    if kind == "ratio":
        value = inputs.ratios.get(arg)
        return (value, None if value is not None else "ratio_unavailable")
    if kind == "severity":
        severity = inputs.fraud_severity if arg == "fraud" else inputs.external_severity
        if severity is None:
            return (_SEVERITY_SCORE["none"], None)  # absence of adverse signal is good news
        return (_SEVERITY_SCORE.get(severity.lower(), 60.0), None)
    if kind == "constant":
        return (factor.constant, None)
    if kind == "ml_pd":
        return (None, _MISSING_ML_REASON)  # Phase-6 model; excluded until then
    return (None, "unknown_source")


def evaluate_scorecard(config: ScorecardConfig, inputs: ScoreInputs) -> RiskResult:
    """Evaluate the scorecard end to end: score → overlays → grade → limit → rate."""
    breakdown: list[FactorResult] = []
    pillar_scores: dict[str, float] = {}
    admitted_pillar_weight = 0.0

    for pillar in config.pillars:
        admitted_weight = 0.0
        factor_results: list[FactorResult] = []
        for factor in pillar.factors:
            raw, reason = _factor_raw(factor, inputs)
            if raw is None:
                factor_results.append(
                    FactorResult(
                        pillar_id=pillar.pillar_id,
                        factor_id=factor.factor_id,
                        raw_value=None,
                        normalized=None,
                        weight=factor.weight,
                        contribution=0.0,
                        admitted=False,
                        exclusion_reason=reason,
                        explanation=f"excluded: {reason}",
                    )
                )
                continue
            normalized = factor.normalizer.apply(raw) if factor.normalizer else raw
            admitted_weight += factor.weight
            factor_results.append(
                FactorResult(
                    pillar_id=pillar.pillar_id,
                    factor_id=factor.factor_id,
                    raw_value=raw,
                    normalized=normalized,
                    weight=factor.weight,
                    contribution=0.0,  # filled after weight renormalization
                    admitted=True,
                    explanation=f"{factor.source} → {normalized:.1f}/100",
                )
            )
        if admitted_weight > 0:
            pillar_score = 0.0
            for fr in factor_results:
                if fr.admitted and fr.normalized is not None:
                    renorm_w = fr.weight / admitted_weight
                    fr.contribution = renorm_w * fr.normalized
                    pillar_score += fr.contribution
            pillar_scores[pillar.pillar_id] = pillar_score
            admitted_pillar_weight += pillar.weight
        breakdown.extend(factor_results)

    total_pillar_weight = sum(p.weight for p in config.pillars)
    coverage = admitted_pillar_weight / total_pillar_weight if total_pillar_weight else 0.0
    if admitted_pillar_weight > 0:
        base_score = sum(
            (next(p.weight for p in config.pillars if p.pillar_id == pid) / admitted_pillar_weight)
            * score
            for pid, score in pillar_scores.items()
        )
    else:
        base_score = 0.0
    base_score = round(base_score, 2)

    final_score, overlays, forced_decision = _apply_overlays(base_score, inputs)
    final_score = round(max(0.0, min(100.0, final_score)), 2)
    grade = _grade(final_score)
    decision = _decide(grade, coverage, forced_decision)

    limit = _compute_limit(config, inputs, decision)
    pricing = _compute_rate(config, grade, inputs, decision)

    notes: list[str] = []
    if any(f.exclusion_reason == _MISSING_ML_REASON for f in breakdown):
        notes.append("ML probability-of-default factor pending Phase 6; excluded from the score.")
    if coverage < 0.7:
        notes.append(f"Evidence coverage {coverage:.0%} below 70% — routed to REFER.")

    return RiskResult(
        scorecard_key=config.key,
        scorecard_version=config.version,
        base_score=base_score,
        final_score=final_score,
        grade=grade,
        decision=decision,
        coverage=round(coverage, 4),
        breakdown=breakdown,
        overlay_log=overlays,
        limit=limit,
        pricing=pricing,
        notes=notes,
    )


def _apply_overlays(
    base_score: float, inputs: ScoreInputs
) -> tuple[float, list[OverlayApplication], Decision | None]:
    score = base_score
    log: list[OverlayApplication] = []
    forced: Decision | None = None

    if inputs.knockouts:
        forced = Decision.DECLINE
        log.append(
            OverlayApplication(
                rule_id="knockout",
                fired=True,
                effect="DECLINE",
                detail=", ".join(inputs.knockouts),
            )
        )

    if inputs.fraud_severity == "critical":
        score = min(score, 45.0)
        log.append(OverlayApplication(rule_id="cap_critical_fraud", fired=True, effect="cap→45"))
    elif inputs.fraud_severity == "high":
        score = min(score, 55.0)
        log.append(OverlayApplication(rule_id="cap_high_fraud", fired=True, effect="cap→55"))

    notch_total = sum(note.notch for note in inputs.qualitative_notes)
    notch_total = max(-12.0, min(12.0, notch_total))  # clamp ±12
    if inputs.qualitative_notes:
        score += notch_total
        log.append(
            OverlayApplication(
                rule_id="qualitative_notch",
                fired=True,
                effect=f"{notch_total:+.1f}",
                detail="; ".join(f"{n.label} ({n.notch:+.0f})" for n in inputs.qualitative_notes),
            )
        )
    return score, log, forced


def _decide(grade: str, coverage: float, forced: Decision | None) -> Decision:
    if forced is not None:
        return forced
    if coverage < 0.7:
        return Decision.REFER
    grade_num = int(grade[2:])
    if grade_num >= 7:
        return Decision.DECLINE
    if grade_num == 6:
        return Decision.REFER
    return Decision.APPROVE


def _compute_limit(config: ScorecardConfig, inputs: ScoreInputs, decision: Decision) -> LimitResult:
    if decision is Decision.DECLINE:
        return LimitResult(arithmetic="declined — no limit")
    fin = inputs.financials
    candidates: list[tuple[str, Decimal]] = []
    if fin is not None and fin.ebitda is not None and fin.ebitda > 0:
        candidates.append(
            ("EBITDA_multiple", Decimal(str(config.ebitda_limit_multiple)) * fin.ebitda)
        )
    if fin is not None and fin.current_assets is not None and fin.current_liabilities is not None:
        mpbf = Decimal(str(config.mpbf_factor)) * (fin.current_assets - fin.current_liabilities)
        if mpbf > 0:
            candidates.append(("MPBF_TandonII", mpbf))
    if inputs.turnover is not None and inputs.turnover > 0:
        candidates.append(
            ("turnover_20pct", Decimal(str(config.turnover_limit_pct)) * inputs.turnover)
        )
    if not candidates:
        return LimitResult(arithmetic="insufficient financials for a limit")
    binding, amount = min(candidates, key=lambda c: c[1])
    arithmetic = "; ".join(f"{name}={value:.0f}" for name, value in candidates)
    return LimitResult(
        amount=amount.quantize(Decimal("1")),
        binding_constraint=binding,
        arithmetic=f"min({arithmetic}) → {binding}",
    )


def _compute_rate(
    config: ScorecardConfig, grade: str, inputs: ScoreInputs, decision: Decision
) -> PricingResult:
    if decision is Decision.DECLINE:
        return PricingResult()
    premium = config.grade_premium.get(grade)
    if premium is None:
        return PricingResult(base_rate_pct=config.base_rate_pct)  # no priced grade
    adjustments: list[tuple[str, float]] = []
    if inputs.collateral_coverage is not None and inputs.collateral_coverage < 1.0:
        adjustments.append(("collateral_shortfall", 0.5))
    if inputs.tenor_years is not None and inputs.tenor_years > 5:
        adjustments.append(("long_tenor", 0.25))
    rate = config.base_rate_pct + premium + sum(a for _, a in adjustments)
    return PricingResult(
        rate_pct=round(rate, 2),
        base_rate_pct=config.base_rate_pct,
        grade_premium_pct=premium,
        adjustments=adjustments,
    )


def credit_appraisal_scorecard() -> ScorecardConfig:
    """The default credit-appraisal scorecard (Five-Cs-aligned pillars, ADR-0009)."""
    return ScorecardConfig(
        key="credit_appraisal",
        version="1",
        pillars=[
            PillarConfig(
                pillar_id="financial_strength",
                weight=0.35,
                factors=[
                    FactorConfig(
                        factor_id="current_ratio",
                        weight=0.25,
                        source="ratio:current_ratio",
                        normalizer=Normalizer(
                            breakpoints=[(0.8, 20), (1.0, 45), (1.33, 65), (2.0, 90)]
                        ),
                    ),
                    FactorConfig(
                        factor_id="interest_coverage",
                        weight=0.25,
                        source="ratio:interest_coverage",
                        normalizer=Normalizer(
                            breakpoints=[(1.0, 15), (1.5, 40), (3.0, 70), (6.0, 95)]
                        ),
                    ),
                    FactorConfig(
                        factor_id="debt_to_equity",
                        weight=0.20,
                        source="ratio:debt_to_equity",
                        normalizer=Normalizer(
                            higher_is_better=False,
                            breakpoints=[(0.5, 95), (1.0, 75), (2.0, 45), (3.0, 20)],
                        ),
                    ),
                    FactorConfig(
                        factor_id="ebitda_margin",
                        weight=0.20,
                        source="ratio:ebitda_margin",
                        normalizer=Normalizer(
                            breakpoints=[(0.03, 20), (0.08, 50), (0.15, 75), (0.25, 95)]
                        ),
                    ),
                    FactorConfig(
                        factor_id="dscr",
                        weight=0.10,
                        source="ratio:dscr",
                        normalizer=Normalizer(
                            breakpoints=[(1.0, 15), (1.25, 45), (1.5, 70), (2.0, 92)]
                        ),
                    ),
                ],
            ),
            PillarConfig(
                pillar_id="ml_default_risk",
                weight=0.20,
                factors=[FactorConfig(factor_id="pd", weight=1.0, source="ml_pd")],
            ),
            PillarConfig(
                pillar_id="external_intelligence",
                weight=0.15,
                factors=[
                    FactorConfig(
                        factor_id="external_signal", weight=1.0, source="severity:external"
                    )
                ],
            ),
            PillarConfig(
                pillar_id="fraud_integrity",
                weight=0.15,
                factors=[
                    FactorConfig(factor_id="fraud_signal", weight=1.0, source="severity:fraud")
                ],
            ),
            PillarConfig(
                pillar_id="qualitative",
                weight=0.15,
                factors=[
                    FactorConfig(
                        factor_id="management_baseline",
                        weight=1.0,
                        source="constant",
                        constant=60.0,
                    )
                ],
            ),
        ],
    )

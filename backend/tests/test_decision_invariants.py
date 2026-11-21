"""Phase-8 decision-integrity invariants (priority #1).

The deterministic scorecard + policy grid is the authority for every number a
credit decision rests on. These tests prove the invariants an LLM could
otherwise erode:

- the LLM has NO structural path to set score / limit / rate / premium /
  decision (``ScoreInputs`` exposes only enumerated, bounded fields);
- knockouts force DECLINE regardless of an otherwise-perfect profile;
- a critical/high fraud signal is a HARD ceiling a positive notch cannot lift;
- litigation (external severity), leverage (PD), collateral and tenor move the
  outputs in the correct, monotonic direction;
- a HITL observation moves the score deterministically and within ±12;
- evaluation is pure and reproducible (same inputs → byte-identical result).

Everything here is pure arithmetic over ``app.analysis`` — no DB, no services.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.analysis.ratios import compute_ratios
from app.analysis.scorecard import (
    QualitativeNote,
    ScoreInputs,
    credit_appraisal_scorecard,
    evaluate_scorecard,
)
from app.docintel.schemas import FinancialLineItems
from app.domain.enums import Decision

# A deliberately STRONG applicant: absent any overlay this profile approves at a
# high grade, so every overlay test below starts from a profile that would
# otherwise pass — proving the overlay, not a weak baseline, drives the outcome.
_STRONG = FinancialLineItems(
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


def _inputs(**overrides: object) -> ScoreInputs:
    base: dict[str, object] = dict(
        ratios=compute_ratios(_STRONG).available_ratios(),
        financials=_STRONG,
        turnover=_STRONG.revenue,
        ml_pd=0.03,
    )
    base.update(overrides)
    return ScoreInputs(**base)  # type: ignore[arg-type]


def _eval(**overrides: object):
    return evaluate_scorecard(credit_appraisal_scorecard(), _inputs(**overrides))


# --------------------------------------------------------------------------- #
# 1. The LLM has no structural channel to authoritative numbers.
# --------------------------------------------------------------------------- #


def test_scoreinputs_exposes_no_freeform_number_channel() -> None:
    """ScoreInputs must never accept a raw score/limit/rate/decision.

    The only human/LLM-influenced field is ``qualitative_notes`` — and each note
    carries an enumerated label plus a bounded ``notch`` (points), never an
    absolute figure. This is the structural guarantee behind invariant #1/#3.
    """
    fields = set(ScoreInputs.model_fields)
    forbidden = {
        "score",
        "final_score",
        "grade",
        "limit",
        "recommended_limit",
        "rate",
        "interest_rate",
        "rate_pct",
        "premium",
        "decision",
    }
    assert forbidden.isdisjoint(fields), (
        f"ScoreInputs leaks an authoritative field: {fields & forbidden}"
    )
    # The one soft channel is bounded by construction.
    assert set(QualitativeNote.model_fields) == {"label", "notch"}


def test_evaluation_is_pure_and_reproducible() -> None:
    """Same inputs → identical result, twice (no hidden state, no I/O, no clock)."""
    a = _eval()
    b = _eval()
    assert a.model_dump() == b.model_dump()


# --------------------------------------------------------------------------- #
# 2. Knockouts force DECLINE unconditionally.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "knockout",
    ["wilful_defaulter", "fraud_conviction", "sanctions_hit", "arbitrary_future_code"],
)
def test_any_knockout_forces_decline_on_an_otherwise_perfect_profile(knockout: str) -> None:
    result = _eval(ml_pd=0.001, knockouts=[knockout])
    assert result.decision is Decision.DECLINE
    assert result.limit.amount is None  # declined → no limit
    assert result.pricing.rate_pct is None  # declined → no rate
    assert any(o.rule_id == "knockout" and o.fired for o in result.overlay_log)


def test_knockout_beats_a_maximally_positive_notch_and_perfect_pd() -> None:
    result = _eval(
        ml_pd=0.001,
        knockouts=["wilful_defaulter"],
        qualitative_notes=[QualitativeNote(label="excellent management", notch=12.0)],
    )
    assert result.decision is Decision.DECLINE


# --------------------------------------------------------------------------- #
# 3. Fraud cap is a hard ceiling the notch cannot lift.
# --------------------------------------------------------------------------- #


def test_critical_fraud_caps_at_45_and_reclamps_after_positive_notch() -> None:
    result = _eval(
        ml_pd=0.01,
        fraud_severity="critical",
        qualitative_notes=[QualitativeNote(label="strong promoters", notch=12.0)],
    )
    assert result.final_score <= 45.0
    assert result.decision is Decision.DECLINE
    assert any(o.rule_id == "fraud_cap_reclamp" and o.fired for o in result.overlay_log)


def test_high_fraud_caps_at_55() -> None:
    result = _eval(ml_pd=0.01, fraud_severity="high")
    assert result.final_score <= 55.0


def test_fraud_none_does_not_cap() -> None:
    capped = _eval(fraud_severity="critical").final_score
    clean = _eval(fraud_severity=None).final_score
    assert clean > capped


# --------------------------------------------------------------------------- #
# 4. Litigation / external adverse severity lowers the score monotonically.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("worse", "better"),
    [("critical", "high"), ("high", "medium"), ("medium", "low"), ("low", None)],
)
def test_worse_external_severity_never_scores_higher(worse: str | None, better: str | None) -> None:
    hi = _eval(external_severity=better).final_score
    lo = _eval(external_severity=worse).final_score
    assert lo <= hi, f"external {worse!r} scored above {better!r}"


def test_severe_litigation_materially_reduces_the_score() -> None:
    clean = _eval(external_severity=None).final_score
    litigated = _eval(external_severity="critical").final_score
    assert clean - litigated >= 5.0  # the external pillar (0.15) moves the needle


# --------------------------------------------------------------------------- #
# 5. PD (ML leverage signal) moves the score monotonically but never decides.
# --------------------------------------------------------------------------- #


def test_pd_sweep_is_monotonic_non_increasing_in_score() -> None:
    pds = [0.005, 0.02, 0.05, 0.10, 0.25, 0.45, 0.60]
    scores = [_eval(ml_pd=pd).final_score for pd in pds]
    for lower, higher in zip(scores, scores[1:], strict=False):
        assert higher <= lower + 1e-9, f"score rose as PD worsened: {scores}"


def test_worst_pd_alone_cannot_force_decline_on_a_strong_profile() -> None:
    # A very high PD lowers the score but the deterministic grade/decision — not
    # the ML signal — owns the outcome; on this strong profile it does not decline
    # purely from PD (PD is 20% of the score, capped in influence).
    result = _eval(ml_pd=0.60)
    assert result.decision in {Decision.APPROVE, Decision.REFER}


# --------------------------------------------------------------------------- #
# 6. Collateral shortfall and long tenor are priced (exposure constraints).
# --------------------------------------------------------------------------- #


def test_collateral_shortfall_raises_the_rate() -> None:
    full = _eval(collateral_coverage=1.5)
    short = _eval(collateral_coverage=0.5)
    assert full.pricing.rate_pct is not None and short.pricing.rate_pct is not None
    assert short.pricing.rate_pct > full.pricing.rate_pct
    assert any(name == "collateral_shortfall" for name, _ in short.pricing.adjustments)


def test_long_tenor_raises_the_rate() -> None:
    short = _eval(tenor_years=3.0)
    long = _eval(tenor_years=7.0)
    assert long.pricing.rate_pct is not None and short.pricing.rate_pct is not None
    assert long.pricing.rate_pct > short.pricing.rate_pct


# --------------------------------------------------------------------------- #
# 7. HITL observation → deterministic, bounded score impact.
# --------------------------------------------------------------------------- #


def test_negative_observation_lowers_score_positive_raises_it() -> None:
    neutral = _eval().final_score
    negative = _eval(qualitative_notes=[QualitativeNote(label="going-concern doubt", notch=-8.0)])
    positive = _eval(qualitative_notes=[QualitativeNote(label="marquee client base", notch=8.0)])
    assert negative.final_score < neutral < positive.final_score


def test_notch_is_clamped_to_plus_minus_twelve() -> None:
    # Even an absurd stack of positive notches cannot add more than +12.
    huge = _eval(
        fraud_severity=None,
        qualitative_notes=[QualitativeNote(label=f"n{i}", notch=20.0) for i in range(5)],
    )
    neutral = _eval()
    assert huge.final_score - neutral.final_score <= 12.0 + 1e-9
    # And symmetrically for negatives.
    huge_neg = _eval(
        qualitative_notes=[QualitativeNote(label=f"n{i}", notch=-20.0) for i in range(5)]
    )
    assert neutral.final_score - huge_neg.final_score <= 12.0 + 1e-9


def test_officer_notch_cannot_flip_a_knockout_or_lift_a_fraud_cap() -> None:
    # These are the two safety rails: a positive officer observation must not be
    # able to rescue a knockout or breach a fraud ceiling.
    ko = _eval(
        knockouts=["wilful_defaulter"], qualitative_notes=[QualitativeNote(label="x", notch=12.0)]
    )
    assert ko.decision is Decision.DECLINE
    capped = _eval(
        fraud_severity="critical", qualitative_notes=[QualitativeNote(label="x", notch=12.0)]
    )
    assert capped.final_score <= 45.0


# --------------------------------------------------------------------------- #
# 8. Coverage gate routes low-evidence cases to REFER.
# --------------------------------------------------------------------------- #


def test_low_coverage_routes_to_refer() -> None:
    # Drop the financial ratios and PD so admitted pillar weight falls below 0.7.
    thin = ScoreInputs(
        ratios={}, financials=None, ml_pd=None, external_severity=None, fraud_severity=None
    )
    result = evaluate_scorecard(credit_appraisal_scorecard(), thin)
    assert result.coverage < 0.7
    assert result.decision is Decision.REFER
    assert any("below 70%" in n for n in result.notes)


# --------------------------------------------------------------------------- #
# 9. Grade + limit + rate are consistent, monotonic pure functions.
# --------------------------------------------------------------------------- #


def test_grade_is_monotonic_in_score() -> None:
    # Sweep PD from best to worst; the numeric grade must never improve as the
    # score falls.
    grades = [int(_eval(ml_pd=pd).grade[2:]) for pd in [0.005, 0.05, 0.20, 0.45, 0.60]]
    for a, b in zip(grades, grades[1:], strict=False):
        assert b >= a, f"grade improved as PD worsened: {grades}"


def test_limit_is_the_binding_minimum_and_scales_with_ebitda() -> None:
    result = _eval()
    assert result.limit.amount is not None
    assert result.limit.binding_constraint in {"EBITDA_multiple", "MPBF_TandonII", "turnover_20pct"}
    # Doubling EBITDA never shrinks the EBITDA-multiple candidate → limit non-decreasing
    # when EBITDA is not the binding floor is not guaranteed, but the min is stable:
    bigger = FinancialLineItems(**{**_STRONG.model_dump(), "ebitda": Decimal("40000000")})
    r2 = evaluate_scorecard(
        credit_appraisal_scorecard(),
        ScoreInputs(
            ratios=compute_ratios(bigger).available_ratios(),
            financials=bigger,
            turnover=bigger.revenue,
            ml_pd=0.03,
        ),
    )
    assert r2.limit.amount is not None and result.limit.amount is not None
    assert r2.limit.amount >= result.limit.amount


def test_decline_yields_neither_limit_nor_rate() -> None:
    result = _eval(knockouts=["wilful_defaulter"])
    assert result.limit.amount is None
    assert result.pricing.rate_pct is None

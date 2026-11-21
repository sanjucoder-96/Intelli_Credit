"""The Five Cs of Credit (ARCHITECTURE §9, hackathon deliverable).

A deterministic mapping from the scorecard's pillars/inputs onto the Five Cs
so the CAM presents Character, Capacity, Capital, Collateral, and Conditions
explicitly. Scores are derived, not invented; the LLM later adds prose that
cites the same evidence.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.analysis.ratios import FinancialRatios
from app.analysis.scorecard import RiskResult, ScoreInputs

_SEVERITY_SCORE = {"none": 95.0, "low": 80.0, "medium": 60.0, "high": 40.0, "critical": 20.0}


class CreditC(BaseModel):
    name: str
    score: float | None
    drivers: list[str] = Field(default_factory=list)


class FiveCs(BaseModel):
    character: CreditC
    capacity: CreditC
    capital: CreditC
    collateral: CreditC
    conditions: CreditC

    def as_list(self) -> list[CreditC]:
        return [self.character, self.capacity, self.capital, self.collateral, self.conditions]


def _pillar_score(result: RiskResult, pillar_id: str) -> float | None:
    """The 0..100 pillar score (its admitted factors' contributions sum)."""
    admitted = [f for f in result.breakdown if f.pillar_id == pillar_id and f.admitted]
    if not admitted:
        return None
    return round(sum(f.contribution for f in admitted), 1)


def derive_five_cs(result: RiskResult, ratios: FinancialRatios, inputs: ScoreInputs) -> FiveCs:
    """Map the scorecard result + ratios + inputs onto the Five Cs."""
    # Character: qualitative baseline + officer notes + external reputation, minus knockouts.
    character_drivers: list[str] = []
    character = _pillar_score(result, "qualitative")
    if inputs.qualitative_notes:
        character_drivers += [f"officer note: {n.label}" for n in inputs.qualitative_notes]
    if inputs.knockouts:
        character = min(character or 50.0, 20.0)
        character_drivers.append("knock-out: " + ", ".join(inputs.knockouts))

    # Capacity: coverage/DSCR + interest coverage + cash-flow quality.
    capacity_ratios = {
        k: v
        for k, v in ratios.available_ratios().items()
        if k in {"interest_coverage", "dscr", "cfo_to_ebitda", "ebitda_margin"}
    }
    capacity = _pillar_score(result, "financial_strength")
    capacity_drivers = [f"{k}={v:.2f}" for k, v in capacity_ratios.items()]

    # Capital: leverage.
    capital_drivers = [
        f"{k}={v:.2f}"
        for k, v in ratios.available_ratios().items()
        if k in {"debt_to_equity", "tol_tnw", "debt_to_ebitda"}
    ]
    capital = None
    if "debt_to_equity" in ratios.available_ratios():
        de = ratios.available_ratios()["debt_to_equity"]
        capital = max(20.0, min(95.0, 100.0 - de * 25.0))

    # Collateral.
    collateral_score = (
        min(95.0, inputs.collateral_coverage * 60.0)
        if inputs.collateral_coverage is not None
        else None
    )
    collateral_drivers = (
        [f"coverage {inputs.collateral_coverage:.2f}x"]
        if inputs.collateral_coverage is not None
        else ["no collateral data"]
    )

    # Conditions: external/macro signal.
    conditions = _SEVERITY_SCORE.get((inputs.external_severity or "none").lower(), 60.0)
    conditions_drivers = [f"external signal severity: {inputs.external_severity or 'none'}"]

    return FiveCs(
        character=CreditC(name="Character", score=character, drivers=character_drivers),
        capacity=CreditC(name="Capacity", score=capacity, drivers=capacity_drivers),
        capital=CreditC(name="Capital", score=capital, drivers=capital_drivers),
        collateral=CreditC(name="Collateral", score=collateral_score, drivers=collateral_drivers),
        conditions=CreditC(name="Conditions", score=conditions, drivers=conditions_drivers),
    )

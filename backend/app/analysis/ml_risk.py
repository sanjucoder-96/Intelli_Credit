"""ML default-risk assessment — the analysis-layer bridge to the PD model.

This is the seam between IntelliCredit's financial evidence and the served PD
model. It builds the canonical feature vector from a borrower's financials,
guards that the model was trained on the *same* feature schema (invariant #5 —
every prediction carries a model/version/feature context, and a schema mismatch
refuses to predict rather than silently mis-scoring), runs the model, and turns
the raw TreeSHAP contributions into labelled, ranked reason codes.

The result is a *relative* default-risk probability plus explanations. It is fed
into the deterministic scorecard as the ml_default_risk pillar's input — it
never sets a score, limit, rate, or decision itself (ADR-0009/0010, invariants
#2, #4). When no model is available, or feature coverage is too low, the result
is marked unavailable and the pillar stays excluded (coverage mechanism intact).
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from app.analysis.features import (
    FEATURE_META,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    PdFeatureVector,
    build_pd_features,
    feature_row,
)
from app.core.metrics import observe_ml_inference
from app.docintel.schemas import FinancialLineItems
from app.infrastructure.ml.pd_model import PdModel


class ReasonCode(BaseModel):
    """A labelled, directional driver of the predicted default risk.

    ``present`` is False when the underlying feature was missing for this
    borrower: the SHAP contribution then reflects the model's missing-value
    branch, not an observed high/low value — surfaced honestly rather than read
    as a real signal.
    """

    feature: str
    label: str
    value: float | None
    present: bool
    shap_log_odds: float
    direction: Literal["increases", "decreases"]


class MlRiskResult(BaseModel):
    """The PD model's contribution for one borrower, with full context."""

    available: bool
    pd: float | None = None
    pd_raw: float | None = None
    coverage: float = 0.0
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    model_name: str | None = None
    model_version: str | None = None
    algorithm: str | None = None
    non_production: bool = False
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    features: dict[str, float | None] = Field(default_factory=dict)
    note: str = ""

    def top_risk_factors(self, k: int = 3) -> list[ReasonCode]:
        return [r for r in self.reason_codes if r.direction == "increases"][:k]

    def top_mitigants(self, k: int = 3) -> list[ReasonCode]:
        return [r for r in self.reason_codes if r.direction == "decreases"][:k]


def assess_default_risk(
    financials: FinancialLineItems | None,
    model: PdModel | None,
    *,
    prior: FinancialLineItems | None = None,
    min_coverage: float = 0.6,
) -> MlRiskResult:
    """Assess relative PD from financials using the champion model, if usable."""
    vec: PdFeatureVector = build_pd_features(financials, prior=prior)
    coverage = vec.coverage()
    features = {name: getattr(vec, name) for name in FEATURE_NAMES}

    if model is None:
        return MlRiskResult(
            available=False,
            coverage=coverage,
            features=features,
            note="No champion PD model registered; ml_default_risk pillar excluded.",
        )
    if list(model.feature_names) != list(FEATURE_NAMES):
        return MlRiskResult(
            available=False,
            coverage=coverage,
            features=features,
            note=(
                "Champion feature schema does not match the current feature "
                f"schema {FEATURE_SCHEMA_VERSION}; refusing to predict."
            ),
        )
    if coverage < min_coverage:
        return MlRiskResult(
            available=False,
            coverage=coverage,
            features=features,
            model_name=model.card.name,
            model_version=model.card.version,
            note=(
                f"Feature coverage {coverage:.0%} below the {min_coverage:.0%} minimum; "
                "PD not estimated, pillar excluded."
            ),
        )

    _t0 = time.perf_counter()
    prediction = model.predict(feature_row(vec))
    observe_ml_inference(True, time.perf_counter() - _t0)
    reason_codes: list[ReasonCode] = []
    for c in prediction.contributions:
        shap = round(c.shap_log_odds, 6)  # round once so sign + value never disagree
        reason_codes.append(
            ReasonCode(
                feature=c.feature,
                label=FEATURE_META.get(c.feature, (c.feature, False))[0],
                value=c.value,
                present=c.value is not None,
                shap_log_odds=shap,
                direction="increases" if shap > 0 else "decreases",
            )
        )
    reason_codes.sort(key=lambda r: abs(r.shap_log_odds), reverse=True)
    note = (
        "Relative default-risk signal from a model trained on foreign public "
        "bankruptcy data; not an absolute Indian PD (ADR-0010)."
    )
    if prediction.non_production:
        note = "NON-PRODUCTION model (see model card). " + note
    return MlRiskResult(
        available=True,
        pd=round(prediction.pd, 6),
        pd_raw=round(prediction.pd_raw, 6),
        coverage=coverage,
        feature_schema_version=prediction.feature_schema_version,
        model_name=prediction.model_name,
        model_version=prediction.model_version,
        algorithm=prediction.algorithm,
        non_production=prediction.non_production,
        reason_codes=reason_codes,
        features=features,
        note=note,
    )

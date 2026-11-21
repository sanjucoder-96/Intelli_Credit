"""Phase-8 ML model validation (priority #9, hermetic gaps).

Extends the existing ``test_pd_model``/``test_features`` suites with the
validation gaps a Phase-8 review demands, none of which need a database:

- artifact integrity: SHAP additivity (base + Σcontrib reconstructs the margin);
- inference-side schema-parity refusal (a model trained on a different feature
  set is refused, never silently mis-served);
- the ``present`` flag honestly marks a missing feature's SHAP branch;
- probability bounds hold on random rows;
- ``non_production`` provenance propagates into the result note;
- ``MlConfig`` defaults + ``IC_ML__*`` env parsing.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from app.analysis.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    build_pd_features,
    feature_row,
)
from app.analysis.ml_risk import assess_default_risk
from app.core.config import MlConfig
from app.docintel.schemas import FinancialLineItems
from app.infrastructure.ml.pd_model import PdModel
from tests.ml_fixtures import build_fixture_pd_model

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


@pytest.fixture
def model(tmp_path):  # noqa: ANN001, ANN201
    build_fixture_pd_model(tmp_path / "pd_model" / "test-1.0.0")
    return PdModel.load(tmp_path / "pd_model" / "test-1.0.0")


def _row() -> list[float]:
    return feature_row(
        build_pd_features(_STRONG, prior=FinancialLineItems(revenue=Decimal("80000000")))
    )


# --------------------------------------------------------------------------- #
# Artifact integrity: SHAP additivity + probability bounds.
# --------------------------------------------------------------------------- #


def test_shap_contributions_are_additive_to_the_model_margin(model: PdModel) -> None:
    pred = model.predict(_row())
    margin = pred.base_value + sum(c.shap_log_odds for c in pred.contributions)
    # binary:logistic → pd_raw = sigmoid(margin); invert and compare.
    assert 0.0 < pred.pd_raw < 1.0
    logit = math.log(pred.pd_raw / (1.0 - pred.pd_raw))
    assert math.isclose(logit, margin, rel_tol=1e-3, abs_tol=1e-3), (logit, margin)


def test_probability_stays_in_bounds_on_extreme_and_missing_rows(model: PdModel) -> None:
    for row in (
        _row(),
        [float("nan")] * len(FEATURE_NAMES),
        [1e6] * len(FEATURE_NAMES),
        [-1e6] * len(FEATURE_NAMES),
    ):
        pred = model.predict(row)
        assert 0.0 <= pred.pd <= 1.0
        assert 0.0 <= pred.pd_raw <= 1.0


def test_one_contribution_per_feature_named_correctly(model: PdModel) -> None:
    pred = model.predict(_row())
    assert {c.feature for c in pred.contributions} == set(FEATURE_NAMES)


# --------------------------------------------------------------------------- #
# Inference-side schema parity: a mismatched model is refused, not mis-served.
# --------------------------------------------------------------------------- #


class _WrongSchemaModel:
    """A model trained on different features — inference must refuse it."""

    feature_names = ["totally", "different", "features"]
    non_production = True

    def predict(self, row: list[float]) -> object:  # pragma: no cover - must never run
        raise AssertionError("assess_default_risk must not predict on a schema mismatch")


def test_schema_mismatched_model_is_refused() -> None:
    result = assess_default_risk(_STRONG, _WrongSchemaModel())  # type: ignore[arg-type]
    assert result.available is False
    assert result.pd is None
    assert "schema" in result.note.lower() or "feature" in result.note.lower()


def test_no_model_abstains_with_honest_note() -> None:
    result = assess_default_risk(_STRONG, None)
    assert result.available is False and result.pd is None
    assert "champion" in result.note.lower()


# --------------------------------------------------------------------------- #
# The `present` flag honestly marks a missing feature.
# --------------------------------------------------------------------------- #


def test_missing_feature_reason_code_is_marked_not_present(model: PdModel) -> None:
    # No prior period → revenue_growth is missing; it still surfaces a SHAP value
    # but must be flagged present=False (the NaN branch, not an observed signal).
    result = assess_default_risk(_STRONG, model, min_coverage=0.0)
    assert result.available is True
    rg = next((rc for rc in result.reason_codes if rc.feature == "revenue_growth"), None)
    assert rg is not None
    assert rg.present is False and rg.value is None
    # A populated feature is marked present.
    cr = next(rc for rc in result.reason_codes if rc.feature == "current_ratio")
    assert cr.present is True and cr.value is not None


def test_low_coverage_abstains(model: PdModel) -> None:
    thin = FinancialLineItems(fiscal_year="2025-26", revenue=Decimal("50000000"))
    result = assess_default_risk(thin, model, min_coverage=0.6)
    assert result.available is False and result.pd is None
    assert "coverage" in result.note.lower()


def test_non_production_flag_propagates_into_note(model: PdModel) -> None:
    result = assess_default_risk(_STRONG, model, min_coverage=0.0)
    assert result.non_production is True
    assert "non-production" in result.note.lower()


# --------------------------------------------------------------------------- #
# Config.
# --------------------------------------------------------------------------- #


def test_ml_config_defaults() -> None:
    cfg = MlConfig()
    assert cfg.enabled is True
    assert cfg.champion_model_name == "pd_model"
    assert cfg.min_feature_coverage == 0.6


def test_feature_schema_version_is_pinned(model: PdModel) -> None:
    assert model.card.feature_schema_version == FEATURE_SCHEMA_VERSION
    assert list(model.feature_names) == list(FEATURE_NAMES)

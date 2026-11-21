"""PD-model serving (Phase 6): artifact loading, prediction shape/bounds,
reason codes, calibration, versioning, and deterministic inference. Uses a tiny
fixture artifact — hermetic, no DB, no training corpus.
"""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.analysis.features import FEATURE_NAMES, build_pd_features, feature_row
from app.analysis.ml_risk import assess_default_risk
from app.docintel.schemas import FinancialLineItems
from app.infrastructure.ml.pd_model import PdModel
from tests.ml_fixtures import build_fixture_pd_model


@pytest.fixture
def model(tmp_path: Path) -> PdModel:
    build_fixture_pd_model(tmp_path / "pd_model" / "test-1.0.0")
    return PdModel.load(tmp_path / "pd_model" / "test-1.0.0")


def _row() -> list[float]:
    fin = FinancialLineItems(
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
    )
    return feature_row(build_pd_features(fin))


def test_model_loads_with_version_context(model: PdModel) -> None:
    assert model.card.name == "pd_model"
    assert model.card.version == "test-1.0.0"
    assert model.card.algorithm == "xgboost"
    assert model.feature_names == list(FEATURE_NAMES)
    assert model.card.non_production is True


def test_prediction_shape_and_bounds(model: PdModel) -> None:
    pred = model.predict(_row())
    assert 0.0 <= pred.pd <= 1.0
    assert 0.0 <= pred.pd_raw <= 1.0
    assert isinstance(pred.base_value, float)
    assert len(pred.contributions) == len(FEATURE_NAMES)
    assert {c.feature for c in pred.contributions} == set(FEATURE_NAMES)
    assert pred.model_name == "pd_model" and pred.model_version == "test-1.0.0"
    assert pred.feature_schema_version == model.card.feature_schema_version


def test_reason_codes_are_finite_and_signed(model: PdModel) -> None:
    pred = model.predict(_row())
    for c in pred.contributions:
        assert math.isfinite(c.shap_log_odds)


def test_wrong_feature_count_raises(model: PdModel) -> None:
    with pytest.raises(ValueError):
        model.predict([0.0, 1.0, 2.0])  # too short


def test_missing_values_are_routed_not_rejected(model: PdModel) -> None:
    row = _row()
    row[FEATURE_NAMES.index("revenue_growth")] = float("nan")
    pred = model.predict(row)
    assert 0.0 <= pred.pd <= 1.0  # NaN routed natively, still predicts


def test_inference_is_deterministic(model: PdModel) -> None:
    a = model.predict(_row())
    b = model.predict(_row())
    assert a.pd == b.pd and a.pd_raw == b.pd_raw
    assert [c.shap_log_odds for c in a.contributions] == [c.shap_log_odds for c in b.contributions]


def test_calibrator_keeps_probability_in_bounds(model: PdModel) -> None:
    # Extreme all-NaN row still yields a bounded calibrated PD.
    pred = model.predict([float("nan")] * len(FEATURE_NAMES))
    assert 0.0 <= pred.pd <= 1.0


def test_assess_default_risk_produces_labelled_reason_codes(model: PdModel, tmp_path: Any) -> None:
    fin = FinancialLineItems(
        revenue=Decimal("100000000"),
        ebitda=Decimal("4000000"),
        net_profit=Decimal("-2000000"),
        total_assets=Decimal("120000000"),
        total_equity=Decimal("12000000"),
        total_debt=Decimal("95000000"),
        current_assets=Decimal("30000000"),
        current_liabilities=Decimal("45000000"),
        inventory=Decimal("20000000"),
        receivables=Decimal("28000000"),
        interest_expense=Decimal("9000000"),
    )
    result = assess_default_risk(fin, model)
    assert result.available is True
    assert result.pd is not None and 0.0 <= result.pd <= 1.0
    assert result.model_name == "pd_model" and result.model_version == "test-1.0.0"
    assert result.non_production is True
    assert result.reason_codes  # labelled + ranked
    top = result.reason_codes[0]
    assert top.label and top.direction in {"increases", "decreases"}
    # reason codes ranked by |shap|
    mags = [abs(rc.shap_log_odds) for rc in result.reason_codes]
    assert mags == sorted(mags, reverse=True)


def test_unavailable_when_no_model() -> None:
    result = assess_default_risk(_some_financials(), None)
    assert result.available is False
    assert result.pd is None
    assert "no champion" in result.note.lower()


def test_unavailable_when_coverage_below_minimum(model: PdModel) -> None:
    # Almost-empty financials → coverage below the 0.6 default minimum.
    result = assess_default_risk(FinancialLineItems(revenue=Decimal("1000000")), model)
    assert result.available is False
    assert result.coverage < 0.6
    assert result.pd is None


def test_real_data_guard_accepts_real_and_rejects_synthetic() -> None:
    from app.infrastructure.ml.artifact import ModelCard
    from app.infrastructure.ml.registry import SyntheticTrainingDataError, assert_real_lineage

    def _card(origin: str) -> ModelCard:
        return ModelCard(
            name="pd_model",
            version="1",
            algorithm="xgboost",
            feature_names=list(FEATURE_NAMES),
            feature_schema_version="1.0",
            data_origin=origin,
            data_lineage={"dataset": "x", "data_origin": origin},
        )

    assert_real_lineage(_card("real"))  # no raise
    with pytest.raises(SyntheticTrainingDataError):
        assert_real_lineage(_card("synthetic_demo"))


def _some_financials() -> FinancialLineItems:
    return FinancialLineItems(
        revenue=Decimal("100000000"),
        total_assets=Decimal("120000000"),
        current_assets=Decimal("50000000"),
        current_liabilities=Decimal("25000000"),
        net_profit=Decimal("9000000"),
        ebitda=Decimal("18000000"),
        total_debt=Decimal("30000000"),
        total_equity=Decimal("60000000"),
    )

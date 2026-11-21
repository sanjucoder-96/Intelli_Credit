"""Test fixtures for the PD model: a tiny, deterministic XGBoost artifact.

Builds a real (but tiny, random-data) model artifact in the exact serving
layout so tests can exercise loading, prediction, calibration, reason codes,
and registry integration without the full training corpus or a champion on
disk. The data is random noise — the model is always marked ``non_production``.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

from app.analysis.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from app.infrastructure.ml.artifact import (
    CALIBRATOR_FILE,
    MODEL_FILE,
    ModelCard,
    write_card,
)


def build_fixture_pd_model(
    directory: str | Path,
    *,
    name: str = "pd_model",
    version: str = "test-1.0.0",
    data_origin: str = "real",
    non_production: bool = True,
) -> ModelCard:
    """Train a tiny XGBoost + isotonic calibrator and write a serving artifact."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    n, d = 500, len(FEATURE_NAMES)
    X = rng.normal(size=(n, d))
    weights = rng.normal(size=d)
    logits = X @ weights
    y = (logits > np.quantile(logits, 0.9)).astype(int)  # ~10% positive

    dtrain = xgb.DMatrix(X, label=y, feature_names=list(FEATURE_NAMES), missing=np.nan)
    booster = xgb.train(
        {"objective": "binary:logistic", "max_depth": 3, "eta": 0.2, "seed": 0},
        dtrain,
        num_boost_round=30,
    )
    raw = booster.predict(dtrain)
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw, y)

    booster.save_model(str(directory / MODEL_FILE))
    joblib.dump(calibrator, directory / CALIBRATOR_FILE)
    card = ModelCard(
        name=name,
        version=version,
        algorithm="xgboost",
        feature_names=list(FEATURE_NAMES),
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        metrics={"roc_auc": 0.9, "pr_auc": 0.5, "brier": 0.05, "ece": 0.02},
        data_lineage={"dataset": "fixture_random", "data_origin": data_origin, "n_rows": n},
        decision_threshold=0.3,
        trained_at="2026-08-21T00:00:00+00:00",
        data_origin=data_origin,
        non_production=non_production,
        notes="Test fixture model trained on random noise — not a real model.",
    )
    write_card(directory, card)
    return card

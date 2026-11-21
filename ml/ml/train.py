"""PD-model training orchestration (ADR-0010).

``run_training`` executes the full honest pipeline on the real Polish public
bankruptcy corpus and writes a serving-ready artifact + model card. XGBoost is
the served champion (its native TreeSHAP powers the reason-code explanations);
LightGBM and a logistic-regression baseline are trained as challenger/baseline
benchmarks and recorded in the card for comparison. Nothing here trains on
synthetic data — the corpus lineage is hashed and stamped ``data_origin='real'``.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb
from app.analysis.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from app.infrastructure.ml.artifact import ModelCard, version_dir

from ml.datasets import load_polish
from ml.pipeline import (
    TrainedModel,
    best_threshold,
    evaluate,
    fit_isotonic,
    global_shap_importance,
    make_split,
    train_lgbm,
    train_logreg_baseline,
    train_xgb,
    write_artifact,
)

FIT_STATEMENT = (
    "Trained on the Polish Companies Bankruptcy public dataset (UCI #365). These "
    "are not Indian corporates; ratio-space distress signals (leverage, coverage, "
    "liquidity, profitability) transfer structurally, so the output is a RELATIVE "
    "default-risk factor re-normalized inside the deterministic scorecard, never an "
    "absolute Indian PD (ADR-0010)."
)


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        sha = out.stdout.strip()
        return sha or None
    except Exception:
        return None


def run_training(
    *,
    data_dir: Path,
    artifact_root: Path,
    version: str,
    horizons: tuple[int, ...] = (1, 2, 3, 4, 5),
) -> dict[str, Any]:
    """Run the full pipeline; return a summary and write the artifact."""
    feature_names = list(FEATURE_NAMES)
    dataset = load_polish(data_dir, horizons)
    split = make_split(dataset.X, dataset.y)

    # Champion: XGBoost (served; native TreeSHAP reason codes).
    booster, xgb_meta = train_xgb(split, feature_names)
    p_test = booster.predict(xgb.DMatrix(split.X_test, feature_names=feature_names, missing=np.nan))
    p_val = booster.predict(xgb.DMatrix(split.X_val, feature_names=feature_names, missing=np.nan))
    uncalibrated = evaluate(split.y_test, p_test)

    # Calibration: isotonic on the held-out validation split. The decision
    # threshold for the confusion view is derived on the CALIBRATED validation
    # predictions (leakage-free) and applied to the calibrated test set.
    calibrator = fit_isotonic(p_val, split.y_val)
    p_val_cal = np.asarray(calibrator.predict(p_val))
    p_test_cal = np.asarray(calibrator.predict(p_test))
    cal_threshold = best_threshold(split.y_val, p_val_cal)
    calibrated = evaluate(split.y_test, p_test_cal, threshold=cal_threshold)
    calibrated["brier_uncalibrated"] = uncalibrated["brier"]
    calibrated["ece_uncalibrated"] = uncalibrated["ece"]

    # Challenger + baseline benchmarks — each threshold derived on its own
    # validation predictions (leakage-free), mirroring the champion.
    _, p_val_lgbm, p_test_lgbm = train_lgbm(split)
    p_val_logreg, p_test_logreg = train_logreg_baseline(split)
    baseline_metrics = {
        "lightgbm_challenger": evaluate(
            split.y_test, p_test_lgbm, threshold=best_threshold(split.y_val, p_val_lgbm)
        ),
        "logistic_regression_baseline": evaluate(
            split.y_test, p_test_logreg, threshold=best_threshold(split.y_val, p_val_logreg)
        ),
    }

    importance = global_shap_importance(booster, split.X_train, feature_names)

    card = ModelCard(
        name="pd_model",
        version=version,
        algorithm="xgboost",
        feature_names=feature_names,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        metrics={**calibrated, "shap_importance": importance},
        baseline_metrics=baseline_metrics,
        data_lineage={**dataset.lineage(), "horizons": list(horizons)},
        decision_threshold=calibrated["threshold"],
        trained_at=datetime.now(UTC).isoformat(),
        git_sha=_git_sha(),
        data_origin="real",
        non_production=False,
        notes=FIT_STATEMENT,
        params={**xgb_meta["params"], "best_iteration": xgb_meta["best_iteration"]},
    )
    directory = version_dir(artifact_root, card.name, version)
    write_artifact(
        directory,
        TrainedModel(
            booster=booster,
            calibrator=calibrator,
            card=card,
            test_metrics_calibrated=calibrated,
            importance=importance,
        ),
    )
    return {
        "artifact_dir": str(directory),
        "n_rows": int(dataset.X.shape[0]),
        "positive_rate": dataset.positive_rate,
        "champion_metrics": calibrated,
        "baseline_metrics": baseline_metrics,
        "top_features": list(importance.items())[:6],
        "lineage": card.data_lineage,
    }

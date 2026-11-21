"""PD-model training pipeline primitives (ADR-0010).

Stages: split → train (XGBoost champion, LightGBM challenger, logistic-regression
baseline) → evaluate (ROC-AUC, PR-AUC, KS, Brier, ECE, confusion @ threshold) →
calibrate (isotonic on a held-out split) → explain (global TreeSHAP importance).
Everything is seeded for reproducibility. Metrics are computed on a held-out
**test** split the models never saw; nothing here fabricates a performance number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import xgboost as xgb
from app.infrastructure.ml.artifact import CALIBRATOR_FILE, MODEL_FILE, ModelCard, write_card
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SEED = 20260821


@dataclass
class Split:
    X_train: np.ndarray
    y_train: np.ndarray
    X_val: np.ndarray
    y_val: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray


def make_split(
    X: np.ndarray, y: np.ndarray, *, test_size: float = 0.2, val_size: float = 0.2
) -> Split:
    """Stratified train/val/test split (test held out; val for early stop + calibration)."""
    X_tr, X_test, y_tr, y_test = train_test_split(
        X, y, test_size=test_size, stratify=y, random_state=SEED
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_tr, y_tr, test_size=val_size, stratify=y_tr, random_state=SEED
    )
    return Split(X_train, y_train, X_val, y_val, X_test, y_test)


def _scale_pos_weight(y: np.ndarray) -> float:
    pos = float((y == 1).sum())
    neg = float((y == 0).sum())
    return neg / pos if pos > 0 else 1.0


def train_xgb(split: Split, feature_names: list[str]) -> tuple[xgb.Booster, dict[str, Any]]:
    params: dict[str, Any] = {
        "objective": "binary:logistic",
        "eval_metric": "aucpr",
        "max_depth": 4,
        "eta": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 5.0,
        "scale_pos_weight": _scale_pos_weight(split.y_train),
        "tree_method": "hist",
        "seed": SEED,
    }
    dtrain = xgb.DMatrix(
        split.X_train, label=split.y_train, feature_names=feature_names, missing=np.nan
    )
    dval = xgb.DMatrix(split.X_val, label=split.y_val, feature_names=feature_names, missing=np.nan)
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=600,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=40,
        verbose_eval=False,
    )
    meta = {"best_iteration": int(booster.best_iteration), "params": params}
    return booster, meta


def _predict_xgb(booster: xgb.Booster, X: np.ndarray, feature_names: list[str]) -> np.ndarray:
    return booster.predict(xgb.DMatrix(X, feature_names=feature_names, missing=np.nan))


def train_lgbm(split: Split) -> tuple[lgb.Booster, np.ndarray, np.ndarray]:
    params = {
        "objective": "binary",
        "metric": "average_precision",
        "num_leaves": 16,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "min_child_samples": 20,
        "scale_pos_weight": _scale_pos_weight(split.y_train),
        "seed": SEED,
        "verbosity": -1,
    }
    dtrain = lgb.Dataset(split.X_train, label=split.y_train)
    dval = lgb.Dataset(split.X_val, label=split.y_val, reference=dtrain)
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=600,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(40, verbose=False), lgb.log_evaluation(0)],
    )
    it = booster.best_iteration
    p_val = np.asarray(booster.predict(split.X_val, num_iteration=it))
    p_test = np.asarray(booster.predict(split.X_test, num_iteration=it))
    return booster, p_val, p_test


def train_logreg_baseline(split: Split) -> tuple[np.ndarray, np.ndarray]:
    """A simple, honestly-worse baseline: median-imputed, scaled logistic regression.

    Returns (validation, test) predicted probabilities so the confusion-matrix
    threshold is derived on validation (leakage-free), like the champion.
    """
    pipe = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=SEED)),
        ]
    )
    pipe.fit(split.X_train, split.y_train)
    return pipe.predict_proba(split.X_val)[:, 1], pipe.predict_proba(split.X_test)[:, 1]


def ks_statistic(y_true: np.ndarray, p: np.ndarray) -> float:
    fpr, tpr, _ = roc_curve(y_true, p)
    return float(np.max(tpr - fpr))


def expected_calibration_error(y_true: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    ece = 0.0
    n = len(p)
    for b in range(bins):
        mask = idx == b
        if not mask.any():
            continue
        conf = float(p[mask].mean())
        acc = float(y_true[mask].mean())
        ece += (mask.sum() / n) * abs(acc - conf)
    return float(ece)


def best_threshold(y_true: np.ndarray, p: np.ndarray) -> float:
    """The probability threshold maximizing F1 (for the confusion-matrix view)."""
    grid = np.unique(np.round(np.linspace(0.02, 0.95, 94), 4))
    best_t, best_f1 = 0.5, -1.0
    for t in grid:
        f1 = f1_score(y_true, (p >= t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t


def evaluate(
    y_true: np.ndarray, p: np.ndarray, *, threshold: float | None = None
) -> dict[str, Any]:
    """Full honest evaluation on a held-out split."""
    thr = threshold if threshold is not None else best_threshold(y_true, p)
    pred = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "roc_auc": round(float(roc_auc_score(y_true, p)), 4),
        "pr_auc": round(float(average_precision_score(y_true, p)), 4),
        "ks": round(ks_statistic(y_true, p), 4),
        "brier": round(float(brier_score_loss(y_true, p)), 5),
        "ece": round(expected_calibration_error(y_true, p), 4),
        "threshold": round(float(thr), 4),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "n": int(len(y_true)),
        "positives": int(int(y_true.sum())),
        "base_rate": round(float(y_true.mean()), 5),
    }


def fit_isotonic(p_val: np.ndarray, y_val: np.ndarray) -> IsotonicRegression:
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(p_val, y_val)
    return iso


def global_shap_importance(
    booster: xgb.Booster, X: np.ndarray, feature_names: list[str], sample: int = 2000
) -> dict[str, float]:
    """Mean |TreeSHAP| per feature over a sample — global explanation."""
    if len(X) > sample:
        rng = np.random.default_rng(SEED)
        X = X[rng.choice(len(X), size=sample, replace=False)]
    contribs = booster.predict(
        xgb.DMatrix(X, feature_names=feature_names, missing=np.nan), pred_contribs=True
    )
    mean_abs = np.abs(contribs[:, :-1]).mean(axis=0)
    ranked = sorted(zip(feature_names, mean_abs, strict=True), key=lambda kv: kv[1], reverse=True)
    return {name: round(float(val), 5) for name, val in ranked}


@dataclass
class TrainedModel:
    booster: xgb.Booster
    calibrator: IsotonicRegression
    card: ModelCard
    test_metrics_calibrated: dict[str, Any]
    importance: dict[str, float]
    extras: dict[str, Any] = field(default_factory=dict)


def write_artifact(directory: Path, model: TrainedModel) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    model.booster.save_model(str(directory / MODEL_FILE))
    joblib.dump(model.calibrator, directory / CALIBRATOR_FILE)
    write_card(directory, model.card)

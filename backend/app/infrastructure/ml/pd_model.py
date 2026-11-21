"""In-process PD model serving (ADR-0010): load a champion artifact, predict a
calibrated probability of default, and expose exact per-feature reason codes.

Inference is deliberately in-process — tree inference is sub-millisecond, so no
model server is warranted. The raw booster margin is turned into a probability,
then an isotonic calibrator (fitted out-of-fold at training time) maps that to a
trustworthy PD. Reason codes are exact TreeSHAP contributions from
``pred_contribs=True`` (no extra ``shap`` dependency, no approximation), in
log-odds space, one per feature plus a base value — the structural
explainability the whole risk engine relies on (invariant #6). Everything is
deterministic: the same feature row always yields the same PD and reason codes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import xgboost as xgb
from pydantic import BaseModel

from app.core.logging import get_logger
from app.infrastructure.ml.artifact import CALIBRATOR_FILE, MODEL_FILE, ModelCard, read_card

logger = get_logger("app.infrastructure.ml.pd_model")


class ReasonContribution(BaseModel):
    """One feature's exact contribution to the model's log-odds margin."""

    feature: str
    value: float | None  # the input feature value (None if it was missing/NaN)
    shap_log_odds: float  # signed TreeSHAP contribution (margin space)


class PdPrediction(BaseModel):
    """A single calibrated PD prediction with full model + reason context."""

    pd: float  # calibrated probability of default in [0, 1]
    pd_raw: float  # uncalibrated booster probability
    base_value: float  # SHAP base (expected log-odds margin)
    contributions: list[ReasonContribution]
    model_name: str
    model_version: str
    algorithm: str
    feature_schema_version: str
    non_production: bool


class PdModel:
    """A loaded champion: booster + isotonic calibrator + model card."""

    def __init__(self, booster: xgb.Booster, calibrator: Any | None, card: ModelCard) -> None:
        self._booster = booster
        self._calibrator = calibrator
        self.card = card

    @classmethod
    def load(cls, directory: str | Path) -> PdModel:
        directory = Path(directory)
        card = read_card(directory)
        booster = xgb.Booster()
        booster.load_model(str(directory / MODEL_FILE))
        calibrator_path = directory / CALIBRATOR_FILE
        calibrator = joblib.load(calibrator_path) if calibrator_path.exists() else None
        logger.info(
            "pd_model_loaded",
            name=card.name,
            version=card.version,
            algorithm=card.algorithm,
            features=len(card.feature_names),
            non_production=card.non_production,
        )
        return cls(booster, calibrator, card)

    @property
    def feature_names(self) -> list[str]:
        return list(self.card.feature_names)

    def _calibrate(self, raw: float) -> float:
        if self._calibrator is None:
            return raw
        out = float(np.asarray(self._calibrator.predict([raw])).ravel()[0])
        return float(min(1.0, max(0.0, out)))

    def predict(self, row: list[float]) -> PdPrediction:
        """Predict a calibrated PD + reason codes for one ordered feature row.

        ``row`` must be in ``card.feature_names`` order; missing entries are NaN
        and routed natively by the tree.
        """
        if len(row) != len(self.card.feature_names):
            raise ValueError(
                f"feature row has {len(row)} values, model expects {len(self.card.feature_names)}"
            )
        matrix = xgb.DMatrix(
            np.asarray([row], dtype=np.float64),
            feature_names=self.card.feature_names,
            missing=np.nan,
        )
        pd_raw = float(self._booster.predict(matrix)[0])
        contribs = self._booster.predict(matrix, pred_contribs=True)[0]
        base_value = float(contribs[-1])
        reasons = [
            ReasonContribution(
                feature=name,
                value=None if not np.isfinite(row[i]) else float(row[i]),
                shap_log_odds=float(contribs[i]),
            )
            for i, name in enumerate(self.card.feature_names)
        ]
        return PdPrediction(
            pd=self._calibrate(pd_raw),
            pd_raw=pd_raw,
            base_value=base_value,
            contributions=reasons,
            model_name=self.card.name,
            model_version=self.card.version,
            algorithm=self.card.algorithm,
            feature_schema_version=self.card.feature_schema_version,
            non_production=self.card.non_production,
        )

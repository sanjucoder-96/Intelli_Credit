"""Model artifact layout — the on-disk contract shared by training and serving.

A trained PD model is a directory ``{artifact_dir}/{name}/{version}/`` holding
three files: the native XGBoost booster (``model.json``), the isotonic
calibrator (``calibrator.joblib``), and a JSON **model card** (``card.json``)
that carries everything needed to serve, explain, and audit the model — the
ordered feature names it was trained on, the feature-schema version, evaluation
metrics, the training-data lineage (dataset id + content hash + origin, so CI
can prove no synthetic data reached training, ADR-0010), the decision threshold,
and an honest-fit statement.

The ``ml`` training package writes this; the backend serves it. Keeping the card
schema here (infrastructure) gives both one definition without the training
package reaching into inference internals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

MODEL_FILE = "model.json"
CALIBRATOR_FILE = "calibrator.joblib"
CARD_FILE = "card.json"


class ModelCard(BaseModel):
    """Serializable metadata that travels with every model version."""

    name: str
    version: str
    algorithm: str  # "xgboost" | "lightgbm" | ...
    feature_names: list[str]
    feature_schema_version: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    baseline_metrics: dict[str, Any] = Field(default_factory=dict)
    data_lineage: dict[str, Any] = Field(default_factory=dict)
    decision_threshold: float = 0.5
    trained_at: str = ""
    git_sha: str | None = None
    data_origin: str = "real"
    # True whenever the model must not be presented as production-grade — e.g.
    # trained on a demo/synthetic stand-in, or on a corpus too small to claim
    # Indian PD calibration. Surfaced beside every prediction.
    non_production: bool = False
    notes: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


def version_dir(artifact_root: Path, name: str, version: str) -> Path:
    return artifact_root / name / version


def write_card(directory: Path, card: ModelCard) -> None:
    (directory / CARD_FILE).write_text(card.model_dump_json(indent=2), encoding="utf-8")


def read_card(directory: Path) -> ModelCard:
    raw = (directory / CARD_FILE).read_text(encoding="utf-8")
    return ModelCard.model_validate(json.loads(raw))

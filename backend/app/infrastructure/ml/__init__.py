"""In-process ML serving for the PD model (ADR-0010).

Training lives in the separate ``ml`` package; this subpackage only *serves*
already-trained champion artifacts, resolved from the ``ml_models`` registry.
"""

from app.infrastructure.ml.artifact import ModelCard
from app.infrastructure.ml.pd_model import PdModel, PdPrediction, ReasonContribution
from app.infrastructure.ml.registry import (
    SyntheticTrainingDataError,
    assert_real_lineage,
    clear_champion_cache,
    get_champion_row,
    load_champion_pd_model,
    register_model,
)

__all__ = [
    "ModelCard",
    "PdModel",
    "PdPrediction",
    "ReasonContribution",
    "SyntheticTrainingDataError",
    "assert_real_lineage",
    "clear_champion_cache",
    "get_champion_row",
    "load_champion_pd_model",
    "register_model",
]

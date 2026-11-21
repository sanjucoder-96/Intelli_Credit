"""ML model registry access (ADR-0010) — the ``ml_models`` Postgres table.

The registry row (:class:`~app.infrastructure.db.models.ModelRegistry`) is the
source of truth for which artifact is the champion. Registration records the
model card's metrics, feature list, and — critically — the training-data
lineage, so a CI guard can prove no ``synthetic_demo`` data ever reached
training. Serving resolves the champion for a model name, loads its artifact
once, and caches it in-process (champions are immutable per version).
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import ModelStage
from app.infrastructure.db.models import ModelRegistry
from app.infrastructure.ml.artifact import ModelCard
from app.infrastructure.ml.pd_model import PdModel

logger = get_logger("app.infrastructure.ml.registry")


class SyntheticTrainingDataError(RuntimeError):
    """Raised when a model's training lineage references non-real data (ADR-0010)."""


def assert_real_lineage(card: ModelCard) -> None:
    """CI/registration guard: refuse any model trained on non-real data.

    The separation between real training data and synthetic demo data is
    structural (ADR-0010): a champion may only be registered if every lineage
    entry is ``data_origin='real'``.
    """
    origins = [card.data_origin]
    lineage = card.data_lineage or {}
    if isinstance(lineage.get("data_origin"), str):
        origins.append(lineage["data_origin"])
    for entry in lineage.get("datasets", []) or []:
        if isinstance(entry, dict) and "data_origin" in entry:
            origins.append(str(entry["data_origin"]))
    bad = sorted({o for o in origins if o != "real"})
    if bad:
        raise SyntheticTrainingDataError(
            f"Model '{card.name}' v{card.version} references non-real training data: {bad}. "
            "Training manifests may reference only data/real/ (ADR-0010)."
        )


async def register_model(
    session: AsyncSession,
    *,
    card: ModelCard,
    artifact_path: str,
    stage: ModelStage = ModelStage.CANDIDATE,
) -> ModelRegistry:
    """Upsert a model version into ``ml_models``; enforce the real-data guard.

    Registering as CHAMPION archives any prior champion of the same name, so at
    most one champion exists per model name.
    """
    assert_real_lineage(card)
    existing = (
        await session.execute(
            sa.select(ModelRegistry).where(
                ModelRegistry.name == card.name, ModelRegistry.version == card.version
            )
        )
    ).scalar_one_or_none()

    if stage is ModelStage.CHAMPION:
        await session.execute(
            sa.update(ModelRegistry)
            .where(
                ModelRegistry.name == card.name,
                ModelRegistry.stage == ModelStage.CHAMPION,
            )
            .values(stage=ModelStage.ARCHIVED)
        )

    feature_list = {
        "feature_names": card.feature_names,
        "feature_schema_version": card.feature_schema_version,
    }
    if existing is not None:
        existing.stage = stage
        existing.metrics = card.metrics
        existing.feature_list = feature_list
        existing.artifact_path = artifact_path
        existing.data_lineage = card.data_lineage
        existing.git_sha = card.git_sha
        row = existing
    else:
        row = ModelRegistry(
            name=card.name,
            version=card.version,
            stage=stage,
            metrics=card.metrics,
            feature_list=feature_list,
            artifact_path=artifact_path,
            data_lineage=card.data_lineage,
            git_sha=card.git_sha,
        )
        session.add(row)
    await session.flush()
    logger.info("model_registered", name=card.name, version=card.version, stage=stage.value)
    return row


async def get_champion_row(session: AsyncSession, name: str) -> ModelRegistry | None:
    return (
        await session.execute(
            sa.select(ModelRegistry)
            .where(ModelRegistry.name == name, ModelRegistry.stage == ModelStage.CHAMPION)
            .order_by(ModelRegistry.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


# In-process champion cache, keyed by (name, version) — versions are immutable.
_CACHE: dict[tuple[str, str], PdModel] = {}


def _resolve_artifact_dir(artifact_path: str, name: str, version: str, settings: Settings) -> Path:
    path = Path(artifact_path)
    if path.exists():
        return path
    # Fall back to the configured artifact root when the stored (possibly
    # another-host) absolute path is missing: the canonical layout is
    # ``{artifact_dir}/{name}/{version}`` (artifact.version_dir).
    return Path(settings.ml.artifact_dir) / name / version


async def load_champion_pd_model(session: AsyncSession, settings: Settings) -> PdModel | None:
    """Resolve + load the champion PD model, cached in-process.

    Returns ``None`` when ML is disabled or no champion is registered — the
    caller then leaves the ml_default_risk pillar excluded, so the deterministic
    scorecard degrades exactly as before Phase 6.
    """
    if not settings.ml.enabled:
        return None
    row = await get_champion_row(session, settings.ml.champion_model_name)
    if row is None:
        return None
    key = (row.name, row.version)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    directory = _resolve_artifact_dir(row.artifact_path, row.name, row.version, settings)
    if not directory.exists():
        logger.warning("champion_artifact_missing", path=str(directory), name=row.name)
        return None
    model = PdModel.load(directory)
    _CACHE[key] = model
    return model


def clear_champion_cache() -> None:
    """Drop the in-process champion cache (tests, or after a promotion)."""
    _CACHE.clear()

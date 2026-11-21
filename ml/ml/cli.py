"""Typer CLI for the IntelliCredit ML package.

Phase 2 ships only the ``info`` command, proving the package installs and the
CLI wiring works. Extended in Phase 6 with the training pipeline commands
(``python -m ml.cli train <model>`` for pd_model / anomaly / revenue_forecast)
per docs/ROADMAP.md and ADR-0010; artifacts register into the ``ml_models``
Postgres table with data-lineage hashes.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(
    name="intellicredit-ml",
    help="IntelliCredit ML training pipelines (ADR-0010).",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """IntelliCredit ML training pipelines (ADR-0010)."""


@app.command()
def info() -> None:
    """Print the ML package status and the model registry location."""
    typer.echo("PD model: `ml train pd_model`; registry: ml_models table (ADR-0010)")


@app.command()
def train(
    model: str = typer.Argument("pd_model", help="Model to train (only pd_model today)."),
    data_dir: str = typer.Option("../data/real/polish", help="Real dataset dir (data/real only)."),
    artifact_dir: str = typer.Option(
        "../backend/storage/models", help="Where to write the served artifact."
    ),
    version: str = typer.Option("1.0.0", help="Model version (semver)."),
) -> None:
    """Train the PD model on real public bankruptcy data and write an artifact."""
    if model != "pd_model":
        raise typer.BadParameter(f"unknown model '{model}' (only pd_model is implemented)")
    from ml.train import run_training

    summary = run_training(
        data_dir=Path(data_dir), artifact_root=Path(artifact_dir), version=version
    )
    typer.echo(json.dumps(summary, indent=2, default=str))


@app.command()
def register(
    artifact_dir: str = typer.Argument(..., help="The model version dir (contains card.json)."),
    stage: str = typer.Option("champion", help="candidate | champion | archived."),
) -> None:
    """Register a trained artifact into the ml_models registry (ADR-0010).

    Enforces the real-data guard and, for ``champion``, archives the prior
    champion. Requires the backend DB env (IC_DATABASE__URL, ...).
    """
    import asyncio

    from app.domain.enums import ModelStage
    from app.infrastructure.db.engine import get_session_factory
    from app.infrastructure.ml.artifact import read_card
    from app.infrastructure.ml.registry import register_model

    directory = Path(artifact_dir).resolve()
    card = read_card(directory)

    async def _do() -> str:
        factory = get_session_factory()
        async with factory() as session:
            row = await register_model(
                session, card=card, artifact_path=str(directory), stage=ModelStage(stage)
            )
            await session.commit()
            return str(row.id)

    row_id = asyncio.run(_do())
    typer.echo(f"registered {card.name} v{card.version} stage={stage} id={row_id}")


if __name__ == "__main__":
    app()

"""Typer CLI for the IntelliCredit ML package.

Phase 2 ships only the ``info`` command, proving the package installs and the
CLI wiring works. Extended in Phase 6 with the training pipeline commands
(``python -m ml.cli train <model>`` for pd_model / anomaly / revenue_forecast)
per docs/ROADMAP.md and ADR-0010; artifacts register into the ``ml_models``
Postgres table with data-lineage hashes.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="intellicredit-ml",
    help="IntelliCredit ML pipelines (Phase-2 stub).",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """IntelliCredit ML pipelines (Phase-2 stub).

    The explicit callback keeps ``info`` a named subcommand (Typer collapses
    single-command apps otherwise); Phase 6 adds the ``train`` command group
    beside it.
    """


@app.command()
def info() -> None:
    """Print the ML package status and where the real pipelines land."""
    typer.echo(
        "ML pipelines land in Phase 6 (docs/ROADMAP.md); registry: ml_models table (ADR-0010)"
    )


if __name__ == "__main__":
    app()

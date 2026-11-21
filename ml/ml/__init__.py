"""IntelliCredit ML package.

Phase-2 stub: only the Typer CLI entrypoint (:mod:`ml.cli`) exists. The real
training pipelines — ``collect -> validate -> preprocess -> features -> split
-> train -> evaluate -> calibrate -> tune -> explain -> serialize -> register``
for the PD model, Isolation-Forest anomaly signal, and revenue forecaster —
land in Phase 6 per docs/ROADMAP.md and ADR-0010. The model registry is the
``ml_models`` Postgres table owned by the backend (ADR-0010); this package
never grows its own registry service.
"""

__version__ = "0.2.0"

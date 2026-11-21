# intellicredit-ml

Training pipelines for IntelliCredit's three honest models — PD classifier,
Isolation-Forest anomaly signal, and revenue forecaster. **Phase 2 status:
package stub only.** Pipelines land in Phase 6 (see `docs/ROADMAP.md` and
`docs/adr/ADR-0010-ml-datasets-and-synthetic-separation.md`).

## Install & run

```bash
cd ml
pip install -e .
python -m ml.cli info
```

## What exists now

- `ml/cli.py` — Typer CLI with a single `info` command.
- Nothing else. No training code, no datasets, no models.

## What arrives in Phase 6

- `collect -> validate -> preprocess -> features -> split -> train -> evaluate
  -> calibrate -> tune -> explain -> serialize -> register` pipeline stages,
  runnable per model from this CLI.
- Training data: **real public datasets only** (`data/real/` — Kaggle US
  bankruptcy, UCI Taiwanese #572, UCI Polish #365). Synthetic demo data
  (`data/synthetic/`) is structurally excluded from every training manifest;
  CI fails on contamination (ADR-0010).
- Registry: the `ml_models` Postgres table owned by the backend (name, version,
  stage, metrics, data-lineage hashes, artifact path) — no MLflow, no extra
  services.

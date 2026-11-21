# ADR-0010: ML: real public datasets for training; SDV synthetic data for demos only; structural separation

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit's second product invariant says numbers come from deterministic code and calibrated models, never from an LLM. That forces the platform to ship genuinely trained models — a probability-of-default (PD) classifier feeding the ML Default Risk pillar of the scorecard (§9–10 of ARCHITECTURE.md), an anomaly signal for the fraud pillar, and a revenue forecaster for forward-DSCR limit sizing. A portfolio-flagship project lives or dies on whether a staff-engineer reviewer believes these pipelines are real: a model "trained" on data invented by the same system that demos it is fakery, and the requirements explicitly ban it.

The tension is that no real Indian corporate default data is obtainable. CIBIL Commercial bureau data, RBI supervisory returns, and lender loss histories are all licensed or confidential; there is no public dataset of Indian mid-corporate defaults with financial statements attached. Meanwhile the demo must present convincing Indian entities — GSTR-3B returns, bank statements with IFSC codes, MPBF-style limit arithmetic — which necessarily means fabricated companies.

The resolution is to split the two concerns and make the split structural. Models train exclusively on real public bankruptcy datasets and are presented with an honest fit statement; demo entities are synthetic, clearly badged, and mechanically excluded from every training manifest. A solo developer on a Windows 11 docker-compose stack also cannot afford an MLOps platform: training must be a CLI pipeline, the registry a Postgres table, and inference in-process. This ADR fixes the datasets, the training pipeline, the synthetic-data generator, and the contamination guards.

## Decision

- **PD model — trained on real public data only.**
  - Primary dataset: **US Company Bankruptcy Prediction** (Kaggle; ~8,262 US firms, 1999–2018) — recent, clean accounting variables that map directly onto our canonical `FinancialFeatureVector`.
  - Robustness benchmarks: **Taiwanese Bankruptcy** (UCI #572; 6,819 firms) and **Polish Companies Bankruptcy** (UCI #365; 5 forecast-horizon files, ~43k firm-year observations). Per-dataset harmonization maps align columns to the shared ~40-dim feature vector so training and inference use one schema.
  - Models: **XGBoost and LightGBM**, class imbalance via `scale_pos_weight`; champion selected on validation PR-AUC, then calibration quality.
  - Splits: **GroupKFold by company** (5-fold) on the US Kaggle panel (the only dataset with company identifiers; Polish and Taiwanese sets use plain stratified KFold) plus a 20 % held-out test set; the Polish multi-horizon data additionally gets **cross-horizon generalization checks** (train on one forecasting horizon, evaluate on others).
  - Calibration: **isotonic regression on out-of-fold predictions**. Tuning: **Optuna** (TPE sampler, MedianPruner, ~100 trials — CPU-fine at this data size).
  - Metrics persisted with every version: ROC-AUC, PR-AUC, KS statistic, Brier score, ECE plus calibration curves. **SHAP TreeExplainer** (global plots + serialized explainer) ships with the model and feeds the reason-code pipeline of §10.
  - **Honest-fit statement, surfaced in the UI:** these firms are not Indian corporates. Ratio-space distress signals (leverage, coverage, liquidity) transfer structurally, so the output is presented as a *relative* default-risk factor re-normalized inside the scorecard. Absolute Indian PD calibration is not claimed anywhere in the product.
- **Anomaly detection — Isolation Forest** on sector-standardized ratio vectors ("is this company's financial shape abnormal for its peers?"). **No supervised fraud classifier is trained**: we hold no real fraud labels, and fraud detection remains primarily the deterministic forensic rules of §11, with this forest contributing one bounded, honestly-labeled `unsupervised_ml` signal.
- **Revenue forecasting** — per-company GST/bank-credit series are 12–36 points, so deep sequence models are the wrong tool: `statsforecast` AutoETS/AutoARIMA per series, with a **global LightGBM** (pooled lag/rolling/seasonality features) as challenger, selected per-series by CV sMAPE.
- **Synthetic data — demo-only, generated honestly.** An **SDV Gaussian copula** is fitted to the *real* datasets' ratio correlation structure; marginals are re-scaled to Indian sector aggregates from RBI's published *Finances of Non-Government Non-Financial Public Limited Companies*; statement line items are back-solved from sampled ratios so demo PDFs and GST returns are internally consistent; fraud demo cases are constructed by injecting rule-triggering patterns (circular counterparty cycles, GSTR-3B↔bank gaps).
- **Separation is structural, not disciplinary:**
  - `data_origin ∈ {REAL_PUBLIC, SYNTHETIC_DEMO}` column on every dataset row and entity;
  - separate `data/real/` vs `data/synthetic/` directory trees;
  - training-set lineage hashes recorded in the model registry;
  - a provenance badge in the UI on every synthetic entity;
  - **CI fails if any training manifest references SYNTHETIC_DEMO**.
- **Registry and serving — no new services.** A Postgres `model_registry` table (name, semver, metrics JSONB, data-lineage hashes, git SHA, artifact path, stage ∈ {candidate, champion, archived}) plus versioned artifacts on a compose volume; serialization is the native booster format (`model.json` / LightGBM text) with a thin joblib'd calibrator wrapper. **Inference is in-process**: champions load at API/worker startup; tree inference is sub-millisecond. A Celery beat job computes per-feature **PSI vs the training reference (> 0.2 → drift alert)** and auto-trains a *candidate*; **promotion is human**, via an admin endpoint gated on AUC ≥ champion − 0.01, ECE ≤ 0.05, KS ≥ champion − 0.02.

## Alternatives considered

### LendingClub as the PD training corpus
The most famous public credit dataset, and wrong for this product: LendingClub records consumer P2P instalment loans — individual borrowers, FICO-style attributes, no balance sheets. A corporate PD model needs firm-level accounting ratios; training on consumer data and presenting the output beside a corporate scorecard would be a category error a credit-literate judge spots immediately. It earns a place only if a retail loan-recommendation workflow is added later.

### Training the PD model on the synthetic Indian data
Superficially attractive — the model would "match" the demo entities. But the synthetic generator has no default outcomes to offer; labels would have to be invented from the same ratio rules the generator uses, so the model would learn back our own copula and rule set, not default behaviour. That is a circular artefact wearing an ML costume — precisely the fakery this project is structured to avoid. Real-but-foreign data with an honest fit statement beats fake-but-local data every time.

### A supervised fraud classifier
Rejected for the same reason: no real fraud labels exist here, and training a classifier on injected synthetic fraud patterns then calling it "ML fraud detection" would be demonstrably hollow. The deterministic forensic rules of §11 are more explainable and more defensible; the Isolation Forest contributes the only honest ML fraud signal available.

### Platt scaling for calibration
Platt's sigmoid assumes a logistic-shaped miscalibration, which fits SVM margins but systematically misfits the score distributions of boosted trees. Isotonic regression is non-parametric, and with thousands of out-of-fold predictions we have ample data to fit it without overfitting the calibration map.

### MLflow for tracking and registry
The prescribed stack suggests it, and it is rejected deliberately: an MLflow tracking server is a fourth stateful service (plus UI and auth surface) for one developer's three models. Everything MLflow would hold — versions, metrics, lineage, stage — fits in one Postgres table in the store we already run, queryable with the same SQL as the rest of the audit trail. The operating cost of another box exceeds its value at this scale by an order of magnitude.

### ONNX export and a dedicated serving layer (BentoML/Triton)
ONNX buys cross-runtime portability we do not need — one Python codebase performs both training and inference, and native booster formats are the best-supported, most debuggable serialization for XGBoost/LightGBM. A separate model server adds network latency and an ops surface in front of sub-millisecond in-process tree inference; at this call volume it is pure cost.

### Deep tabular models (TabNet, FT-Transformer)
On tabular data of this size (~8–10k rows), gradient-boosted trees remain the accuracy benchmark, train in seconds on CPU, and come with exact TreeExplainer SHAP — the explainability the entire risk engine is built around. Deep tabular models would demand GPU-tinged training, approximate attribution, and hyperparameter fragility, for accuracy that published benchmarks show is at best equal here.

## Consequences

**Positive.** Every ML claim in the product survives scrutiny: the training data is public and citable, the pipeline (`collect → … → register`, `python -m ml.train pd_model`) is reproducible end-to-end on a laptop CPU, and the UI never implies a calibration it does not have. Demo entities are internally consistent and Indian-flavoured (ratios respect RBI sector aggregates; statements reconcile) while being impossible to smuggle into training — contamination requires defeating a CI gate, a lineage hash, and a schema column simultaneously. Zero new stateful services; the model registry shares Postgres's backup and migration story (Alembic, per ADR-0002).

**Negative / accepted trade-offs.** US, Taiwanese, and Polish firms are not Indian mid-corporates: sector mix, accounting conventions, and base default rates differ, which is why the PD output is confined to a *relative* factor worth 20 % of the scorecard and never an absolute Indian PD. Harmonizing three datasets onto one ~40-dim vector is lossy (the Taiwanese set's 95 ratios collapse; some canonical features are dataset-missing and lean on native NaN routing). RBI's company-finances aggregates are coarse — broad sectors, annual — so synthetic marginals are approximations, acceptable for demos only. Without realized Indian defaults, monitoring is PSI-based feature drift, not true performance drift; the champion could degrade silently on dimensions PSI does not see, which the human promotion gate mitigates but cannot eliminate.

**Follow-up obligations.**
- Build and version the per-dataset harmonization maps and `pandera` validation schemas alongside the training pipeline; dataset download/licensing notes checked into `data/real/README`. Note that SDV is licensed under the Business Source License 1.1 (DataCebo) — free for non-production/demo use but not open source for commercial production; commercial deployment would require a DataCebo license or a swap to a permissively licensed Gaussian-copula implementation (the fitted-copula approach is library-agnostic).
- Implement the CI training-manifest guard and the `data_origin` guard inside the pipeline's Validate stage in the same phase as the first trained model — the guard must exist before the first champion is promoted.
- The synthetic generator additionally feeds the scanned-document fabrication pipeline for the OCR golden corpus (Phase 6/8 deliverable per ARCHITECTURE.md §14) — its ratio back-solver must be built API-first, not demo-script-only.
- Ship the admin promotion endpoint with the metric gates, and an officer "record eventual outcome" feedback flow so the retrain-promote loop is demonstrable end-to-end; revisit whether accumulated real officer labels ever justify a calibration refresh.
- Document the RBI publication vintage used for sector marginals and refresh it when a newer annual release appears.

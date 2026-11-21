# IntelliCredit — Final Project Status

Date: 2026-09-17. All numbers below are measured from the repository this session,
or explicitly marked otherwise. Nothing is invented.

```text
IntelliCredit
────────────────────────────────────────────────

Phase 1   Architecture                     COMPLETE
Phase 2   Skeleton                         COMPLETE
Phase 3   Database                         COMPLETE
Phase 4   Ingestion / APIs                 COMPLETE
Phase 5   Agents / Workflow engine         COMPLETE
Phase 6   ML                               COMPLETE
Phase 7   Frontend                         COMPLETE
Phase 8   Evaluation / adversarial tests   COMPLETE
Phase 9   Production hardening             COMPLETE
Phase 10  Documentation / demo             COMPLETE
```

Each phase has a report under `docs/` (Phase reports 2–9). Phase 10's deliverables
are this document plus the judge-facing package (README, submission, demo, setup,
architecture walkthrough, screenshots).

---

## Test counts (measured this session)

| Suite | Result |
|---|---|
| Backend unit (`pytest -m "not integration"`) | **267 passed, 44 skipped, 0 failed** (9.8s) |
| Backend test files / test functions | 50 files · 287 `def test_` functions |
| Backend integration (`IC_INTEGRATION_TESTS=1`, +44 skipped-by-default) | see *Deployment / live* below |
| Frontend `vitest` | **15 passed** (3 files) |

The 44 skipped backend tests are the integration suite; they run only with
`IC_INTEGRATION_TESTS=1` against a live Postgres/Redis. The largest full-regression
figure recorded in the project is **293 passed** (unit + integration, Phase-8 run).

## Quality gates (measured this session — all green)

| Gate | Result |
|---|---|
| Backend `mypy app` | clean — **138 source files** |
| Backend `ruff check` | clean |
| Backend `import-linter` | **2 contracts kept, 0 broken** (clean-architecture layers + core-kernel) |
| Frontend `tsc --noEmit` | clean |
| Frontend `eslint` | clean |
| Frontend `vitest` | 15 passed |
| Frontend `next build` | success (verified earlier this session) |

## Model card (measured — `pd_model@1.0.0`, held-out test, Polish UCI #365)

| Metric | Champion (XGBoost, calibrated) |
|---|---|
| ROC-AUC | 0.8125 |
| PR-AUC | 0.3132 |
| KS | 0.4905 |
| Brier | 0.03836 |
| ECE | 0.0034 (from 0.2643 uncalibrated) |

Isotonic calibration; exact TreeSHAP reason codes; 14-feature schema shared by
training and inference. Trained on **foreign** public bankruptcy data → presented
as a **relative** default-risk signal (one 20% scorecard factor), **not** an Indian
PD. Dataset n = 43,405 firm-years, base default rate ~4.8%. Source of truth:
`backend/storage/models/pd_model/1.0.0/card.json`.

## Deterministic extraction (measured)

classification 1.0 · field precision 1.0 · field recall 1.0 · numeric 1.0 ·
page-citation 1.0 · **fabrications 0** · adversarial unknown-preserved 2/2. Scope:
GSTR-3B / 2B / 2A + bank statements only; corpus deliberately small (wide Wilson
CIs, reported). LLM extraction of financial statements/ITR/annual reports is
**UNMEASURED** (nondeterministic, key-gated).

## Hot-path latency (measured, deterministic, p50)

GSTR-3B extraction ~0.2 ms · ratios+scorecard ~0.2–0.5 ms · PD feature build
~0.05 ms · CAM assemble+render ~0.3–1 ms.

---

## Known limitations (honest)

- **Indian PD calibration** — the PD model is trained on foreign public data; it is
  a *relative* signal, not an absolute Indian PD (no realized Indian default data is
  obtainable). Labeled in-product.
- **LLM extraction accuracy** (financial statements/ITR/annual reports) and
  **web-research recall/precision** are **UNMEASURED** — nondeterministic and
  key-gated. Labeled corpora + runners exist to measure them when keys are supplied.
- **One workflow implemented** — `credit_appraisal` (v3). The config-driven
  mechanism for adding workflows is built and unit-tested; the other eleven are a
  scheduled extensibility path, and three of five config surfaces
  (document pack, scorecard/policy, report template) remain typed Python not yet
  lifted into the YAML.
- **Deferred-by-design ML** — Isolation-Forest anomaly model, revenue forecasting,
  SDV synthetic generation, and PSI drift monitoring are described in the
  architecture but **not built**.
- **DOCX export** deferred behind PDF (PDF is the demo path).
- **Automated E2E** (Playwright) is future work; frontend verification was manual
  in-browser. Concurrent-load numbers come from the provided harness, not a
  production benchmark.
- **Observability** is Prometheus text on `/metrics`; no bundled Grafana.

---

## Deployment / live verification status

**Static:** `docker compose config` validates; compose is hardened (one-shot
`migrate` service, `restart: unless-stopped`, healthchecks, persistent volumes for
Postgres/Redis/ML artifacts) — see [DEPLOYMENT.md](DEPLOYMENT.md) and
[PHASE9_REPORT.md](PHASE9_REPORT.md).

**Live stack (this session):** _to be finalized below after the clean
`docker compose up --build` completes this session; prior phases were blocked by an
unresponsive host Docker daemon and did not fake a result._

<!-- LIVE-RESULTS -->

---

## Demo status

- Offline-capable demo: `make seed` + `scripts.provision_demo` +
  `scripts.provision_documents` (scripted caller — no API keys). One case completed
  (APPROVE), one paused at HITL.
- Five-minute judge script: [HACKATHON_DEMO.md](HACKATHON_DEMO.md).
- Durable kill-and-resume proof: `scripts.chaos_demo`.
- Run-mode honesty badge (LIVE / REPLAY / DEGRADED) in the case header.

## Documentation status

Judge-facing package complete: [README](../README.md),
[HACKATHON_SUBMISSION](HACKATHON_SUBMISSION.md), [HACKATHON_DEMO](HACKATHON_DEMO.md),
[DEMO_SETUP](DEMO_SETUP.md), [ARCHITECTURE_WALKTHROUGH](ARCHITECTURE_WALKTHROUGH.md),
[SCREENSHOTS](SCREENSHOTS.md), this file; plus deep reference
([ARCHITECTURE](ARCHITECTURE.md), [16 ADRs](adr/), [DEPLOYMENT](DEPLOYMENT.md),
[DEVELOPMENT](DEVELOPMENT.md), phase reports). API `/docs` carries an app-level
description, per-tag descriptions, and a documented auth flow.

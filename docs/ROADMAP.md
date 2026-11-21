# IntelliCredit AI — Build Roadmap

Ten phases. **A phase begins only when the previous phase's acceptance criteria are met.** Every phase ships production-quality output — no placeholders, no TODOs, no mocked APIs.

The sequencing follows the **harvest-the-abstraction** rule from [ARCHITECTURE.md §4](ARCHITECTURE.md): credit appraisal is built end-to-end concretely first; vendor risk and ESG follow; the workflow DSL is frozen only after three real workflows have shaped it; all 12 launch workflows exist as pure YAML before the extensibility mechanism is declared done.

---

## Phase 1 — Architecture ✅ (complete)

**Deliverables:** [ARCHITECTURE.md](ARCHITECTURE.md) · 16 ADRs in [adr/](adr/) · [RISK_REGISTER.md](RISK_REGISTER.md) · this roadmap.
**Method:** three independent architecture proposals (simplicity / DDD / agentic-depth lenses) + three subsystem deep-dives (agent runtime, document intelligence + RAG, risk/ML), scored by a three-judge panel; winner synthesized with grafts and the panel's cross-cutting warnings addressed as first-class sections (§15 threat model, §6 evidence permanence, §14 eval harness).

## Phase 2 — Folder structure & project skeleton

**Objective:** the repository compiles, lints, and runs empty — every module boundary from ARCHITECTURE.md §3 exists and is mechanically enforced.

- Monorepo layout: `backend/` (domain/application/infrastructure/agents/workflows/api/tasks), `ml/`, `frontend/`, `docs/`, `data/{real,synthetic}/`, `deploy/`.
- Tooling: `uv` (or pip-tools) lockfiles; `ruff` + `mypy --strict` + `import-linter` contracts encoding the dependency rule; pre-commit hooks; `pnpm` + ESLint + strict TS config for the frontend.
- `pydantic-settings` configuration skeleton, `.env.example`, structlog bootstrap with contextvar binding.
- docker-compose with all core services (postgres/redis/api/worker/beat/frontend) + `--profile ops`; Windows-safe Celery pool wiring; Makefile (`make up`, `make test`, `make demo-seed` stubs that fail loudly, not silently).
- CI pipeline (GitHub Actions): lint, typecheck, import-linter, unit tests, frontend build.

**Acceptance:** `docker compose up` brings up all services healthy; `/health` returns green; CI is green; an import that violates layering fails CI.

## Phase 3 — Database

**Objective:** the complete relational model, migrated and seeded.

- Alembic from the first table. Core schema: tenants/users/roles, companies, cases, documents (+ `document_stages` checkpoints), chunks (text + tsvector generated column + pgvector column), workflow_definitions (immutable versions), workflow_runs, agent_steps (+ gzipped payload side table), findings (append-only, supersession links), evidence, llm_calls, scorecard_configs, score_breakdowns, model_registry, feature_flags, memory_items, audit_log.
- Value objects with validation: CIN, GSTIN, PAN, IFSC.
- Postgres RLS policies per tenant-scoped table; repository base class with mandatory `TenantContext`; SQLAlchemy 2.0 async engine + session management for API and Celery.
- Hybrid-retrieval SQL (FTS + pgvector + RRF in one CTE) implemented and benchmarked against a seeded corpus.
- Seed scripts: demo tenant, users per role, empty demo cases.

**Acceptance:** migrations apply from zero; RLS verified by a cross-tenant test that must fail both with and without repository scoping; hybrid retrieval returns fused results under 100 ms on seeded data.

## Phase 4 — Backend APIs & ingestion pipeline

**Objective:** documents in, structured JSON out — the full document-intelligence pipeline plus the platform API surface.

- Auth (JWT access/refresh, argon2), RBAC middleware, audit log.
- Case/document APIs; upload via `StoragePort` (local FS backend, content-addressed); `SourceConnector` port with the upload connector.
- The ten-stage Celery ingestion chain (ARCHITECTURE.md §7) with `document_stages` checkpointing, idempotent task IDs, per-stage retry; SSE progress relay (Redis pub/sub → EventSource endpoint).
- OCR chain: RapidOCR primary, Gemini Flash/Pro vision escalation, validator gating; pdfplumber + Gemini-vision table extraction.
- `DocumentTypeRegistry` with the initial taxonomy (GSTR-1/2A/2B/3B, ITR, bank statement, financial statements, CIBIL Commercial, sanction letter, legal notice, board minutes, …) and per-type extraction schemas + deterministic post-validators.
- Cross-document validators: GSTR-2A/2B↔3B ITC reconciliation, GST↔bank credits, ITR↔GST turnover, cheque returns.
- Chunking profiles, fastembed embedding, indexing; hybrid retrieval API; feature-flag service.

**Acceptance:** upload a mixed bundle (born-digital PDF + scanned GST return + XLSX + ZIP) → every document reaches `READY` with typed extractions, citations carry page/bbox, validators emit findings with citations into both source documents; a killed worker resumes without duplicate rows; OpenAPI spec generates the typed FE client.

## Phase 5 — AI agents & workflow engine

**Objective:** the full agentic pipeline for **credit appraisal**, end-to-end, plus the workflow engine it runs on.

- `BaseAgent` runtime: plan/act/reflect/revise-once lifecycle, `AgentContext` DI, `StructuredCaller` (response_schema + Pydantic + repair loop), confidence algebra (weighted geometric mean, per-agent weights), retry ladder with degraded-parking, budgets + per-run cost circuit breaker.
- `LLMGateway`: Pro/Flash tiering, Redis prompt-hash cache, global token-bucket rate limiter, `llm_calls` accounting, record/replay mode.
- LangGraph integration: Postgres checkpointer, `Send` fan-out, conditional edges, `interrupt()` HITL with typed `ReviewRequest`/`ReviewResponse`; Celery task ownership with checkpoint-resume semantics.
- The 12 agents (most as configured `GenericLLMAgent` instances): DocumentParsing, OCR, FinancialAnalysis (deterministic ratio engine), Research + News (budgeted supervisor subgraph, Tavily tools, web-evidence snapshots), Compliance, FraudDetection (rule harness), RiskAssessment, Recommendation, CAMGenerator, ReportWriter, Presentation.
- `GraphCompiler` v1 + `credit_appraisal` YAML definition (immutable versioned registration); memory tiers incl. episodic `memory_items` + Distiller node.
- Prompt-injection defenses: instruction/evidence structural separation, source-tier tainting.

**Acceptance:** a seeded case runs analyze → HITL interrupt → resume → score → decide → report with live SSE trace; the officer note visibly re-scores with a logged delta; every CAM claim resolves to an evidence row; a mid-run worker kill resumes from checkpoint without double-writing evidence; replay mode reruns the full graph byte-identically with no API key.

## Phase 6 — ML models

**Objective:** real trained models, honestly evaluated, integrated as scorecard factors.

- `ml/` Typer CLI pipeline: collect (download + verify public datasets: US bankruptcy/Kaggle, Taiwan/UCI 572, Polish/UCI 365) → validate (pandera) → preprocess → `FinancialFeatureVector` harmonization → GroupKFold split → XGBoost + LightGBM training → isotonic calibration → evaluation (ROC-AUC, PR-AUC, KS, Brier, ECE) → Optuna tuning → SHAP explain → serialize → register (Postgres registry, candidate stage).
- IsolationForest peer-abnormality model; statsforecast + global-LightGBM revenue forecaster.
- Reason-code generator (SHAP → grouped, sector-percentile templates); `ExplanationBundle`.
- Scorecard engine: `ScorecardConfig` registry, normalizers, overlays, coverage, decision policy, limit/rate formulas (MPBF Tandon II, EBITDA multiple, turnover method, DSCR); policy-grid enforcement in the Recommendation agent.
- Synthetic demo-data generator (SDV Gaussian copula calibrated to RBI aggregates; statement back-solving; fraud-pattern injection) + the **scanned-document fabrication pipeline** (render → skew/noise/stamp degradation) producing the golden messy-scan corpus.
- Drift monitoring (PSI beat job), human-gated promotion endpoint; CI guard: training manifests must not reference `SYNTHETIC_DEMO`.

**Acceptance:** `python -m ml.train pd_model` reproduces the champion from raw downloads with pinned seeds; holdout metrics persisted and honest; SHAP reason codes render in English with sector percentiles; scorecard + overlays produce a printed arithmetic trail; fraud demo case triggers the intended rule signals and caps.

## Phase 7 — Frontend

**Objective:** the full UI, better-looking than contemporary SaaS, entirely server-driven per workflow.

- App shell: auth, org switcher, dark mode, Framer Motion transitions; typed API client generated from OpenAPI.
- Portfolio dashboard; case workspace (documents / pipeline progress / findings); live run timeline (SSE agent steps with confidence bands and cost).
- Server-driven workflow dashboards: the seven-block vocabulary (risk_gauge, table, timeline, heatmap, network, narrative, evidence_chain) rendered from `GET /workflows/{id}/manifest` — ECharts everywhere.
- Document viewer (pdf.js) with citation-pin overlays and `?doc&page&highlight` deep links; report viewer (HTML IR) with hover-to-evidence; the "Explain" tab replaying AgentStep/Evidence with confidence-component breakdowns and score arithmetic.
- Company-profile page (identity/CIN/GSTIN, shareholding, financial snapshot, run history, episodic-memory highlights) and research feed (Research/News findings + beat-driven news sweeps with citations and source tiers).
- Credit-officer HITL portal: `ReviewRequest`-schema-rendered forms; qualitative-note mapping confirmation UX.
- AI chat scoped to case evidence (RAG + streamed tokens).
- `vendor_risk` workflow authored as pure YAML — the second workflow, proving server-driven dashboards need zero frontend changes.

**Acceptance:** the entire credit-appraisal demo path is navigable start-to-finish; dashboards render for a second workflow (vendor risk) with zero frontend changes; dark mode is complete; Lighthouse performance/accessibility ≥ 90 on the dashboard.

## Phase 8 — Testing & evals

**Objective:** the quality harness that makes agent regressions visible and the demo path unbreakable.

- Backend: unit suites (domain pure, application with fakes), schema contract tests for all 12 agent outputs **including Gemini response-schema compatibility**, integration tests on the compose test profile, idempotency tests (kill/retry at every seam), RLS/tenancy tests.
- Eval harness as versioned artifacts: extraction field-F1 on the golden messy-scan corpus; retrieval hit-rate/MRR; LLM-judge groundedness + citation precision; recommendation-vs-policy consistency; **prompt-injection resistance suite**; confidence reliability curves.
- Golden-file CAM tests on seeded data; frontend Vitest + Testing Library; Playwright E2E over the exact demo path; latency budget assertions on the fast-path variant.
- Third workflow (`esg_analysis`) authored as pure YAML (`vendor_risk` landed in Phase 7) — the three-workflow DSL-freeze checkpoint; remaining nine definitions authored and smoke-run.

**Acceptance:** CI runs the full pyramid without API keys (replay fixtures); eval scores are recorded artifacts with baselines; all 12 workflow definitions register and compile; the Playwright demo path passes on a cold `docker compose up`.

## Phase 9 — Deployment

**Objective:** anyone can run it; the demo is bulletproof.

- Hardened images (multi-stage builds, non-root, pinned digests); compose profiles (core / ops / test); resource limits tuned for a 16 GB laptop.
- `make demo-seed`: demo tenant, three seeded cases (clean approve, fraud-flagged decline, HITL-refer), pre-warmed LLM cache, one pre-recorded replay run.
- Backup/restore scripts (single Postgres + volume story); env validation with actionable errors; optional single-VM cloud deployment guide (compose on a VPS behind Caddy).

**Acceptance:** fresh clone → `.env` from example → `docker compose up` → `make demo-seed` → full demo in under 15 minutes on a clean machine, including fully offline via replay mode.

## Phase 10 — Documentation

**Objective:** the repository reads like a product.

- Root README (positioning, screenshots, quickstart, architecture summary); SETUP.md; API docs (generated OpenAPI + guide); database schema doc (ERD generated from models); agent & workflow authoring guide ("add workflow #13 in a day" tutorial — the extensibility proof); ML model cards (datasets, metrics, honest-fit statements); demo script with timings; updated architecture diagram set.
- Hackathon adaptation playbook: the checklist for retargeting the platform to a new vertical in days (new YAML, document pack, scorecard, template).

**Acceptance:** a newcomer follows SETUP.md to a working demo without asking questions; the "workflow #13" tutorial has been executed once for real as a dry run.

# Phase 3 Completion Report — Database

**Date:** 2026-07-17 · **Status:** ✅ complete, all acceptance criteria verified against live Postgres 17 + pgvector

## 1. Acceptance criteria — verification evidence

| Criterion (ROADMAP Phase 3) | Evidence |
|---|---|
| Migrations apply from zero | `0001 → 0002` upgrade clean; integration round-trip test proves **base → 24 tables + pgvector → base → head** |
| **RLS verified by a cross-tenant test that must fail with either layer disabled** | `tests/test_rls.py`: **layer 1** (repository `scoped()` choke point) blocks cross-tenant reads on a superuser connection *with a control query proving the test detects leaks*; **layer 2** (RLS policies on the non-superuser app role) blocks raw-SQL cross-tenant reads, blocks unset-GUC reads (default deny), and blocks cross-org INSERTs (`WITH CHECK`); plus a **policy-coverage test** asserting every org-carrying table in the metadata is RLS-enabled, `FORCE`d, and policied (from `pg_class`/`pg_policies`) |
| Hybrid retrieval fused + **< 100 ms** on seeded data | `tests/test_hybrid_retrieval.py`: 5,000-chunk synthetic corpus; a needle chunk (unique GSTIN token + exact query vector) wins **both arms (rank 1/1)** and the fusion; results RRF-ordered; **P50 latency asserted < 100 ms**; tenant-filter test returns zero rows for a foreign org |
| Seed scripts | `python -m app.seeds.demo` (also `make seed`): demo org, analyst/credit-officer/admin users, 3 companies (format-valid synthetic GSTINs, `SYNTHETIC_DEMO`-labeled), 2 draft cases; **idempotent** (verified by double-run); bootstraps the RLS app role |
| Full quality gates | ruff + format ✓ · mypy --strict ✓ (76 files) · import-linter 2 contracts ✓ · 58 unit + **7 integration** tests ✓ · live stack smoke ✓ (seed in-container; random-org `tenant_session` sees 0 rows) |

## 2. What was built

**Schema (migration `0002_phase3_schema_rls`, 24 tables total):**
- `chunks` — the hybrid-retrieval unit: text + metadata + `vector(384)` (HNSW, cosine, m=16/ef=64) + **generated** `tsvector` column (GIN) in one row, per ADR-0002; dimension pinned to the ADR-0007 default embedder
- `memory_items` — episodic memory per company, same vector+FTS mechanics (Distiller writes in Phase 5)
- `document_stages` — per-(document, stage) ingestion checkpoints with `attempt`/`error` (the ARCHITECTURE §15.3 idempotency substrate; Phase-4 pipeline state)
- `findings` — **append-only** with `superseded_by_id` supersession links (RESTRICT), the six-category risk taxonomy tag (`business/financial/operational/legal/reputational/macro`, ARCHITECTURE §9), severity, confidence, source tier, and a partial index over current findings
- `scorecard_configs` — immutable versioned scorecard JSON (unique key+version, checksummed) mirroring workflow-definition governance (ADR-0009)
- `score_breakdowns` — one row per factor contribution so the CAM prints exact arithmetic (Phase 6 populates)
- `agent_execution_payloads` — gzipped prompt/output side table (ADR-0012), unique per agent execution

**Row-level security (ADR-0013 second layer):**
- Policies on **all 18 org-carrying tables**, `ENABLE` + `FORCE`, keyed on the transaction GUC `app.current_org_id` (`NULLIF(current_setting(...), '')::uuid` — unset ⇒ zero rows, default deny)
- Special policies: `feature_flags` (tenants read global rows, write only their own org's) and `llm_interactions` (NULL-org platform rows are admin-only)
- `tenant_session(ctx)` in `engine.py`: transaction on the **non-superuser app-role engine** with `set_config(..., is_local ⇒ true)` — pooled connections can never leak tenant identity; refuses to run if `IC_DATABASE__RLS_URL` is unconfigured (no silent fallback to the policy-bypassing admin engine)
- `rls.py`: idempotent `ensure_app_role()` (role + grants + `ALTER DEFAULT PRIVILEGES`; the app role owns nothing, so it cannot alter the policies that bind it); dev DSN derivation helper; wired into compose env, `.env.example`, seeds, and test fixtures

**Hybrid retrieval (`infrastructure/db/retrieval.py`):** the ADR-0002 mechanism as one SQL statement — FTS arm (`websearch_to_tsquery` + `ts_rank_cd`) and vector arm (`<=>` cosine) as CTEs with identical tenant/case/doc-type/period filters, `FULL OUTER JOIN` fusion with **RRF (k=60)**, `SET LOCAL hnsw.iterative_scan = relaxed_order` (pgvector ≥ 0.8) so selective filters don't starve the ANN arm; typed `RetrievedChunk` results carrying both arms' diagnostic ranks.

**Review carry-ins from Phase 2 (all landed):** 7 FK indexes on obvious query paths (`risk_assessments/llm_interactions/reports.workflow_execution_id`, `loan_applications.company_id`, `evidence.loan_application_id/document_id`, `workflow_executions.workflow_id`); 3 CHECK constraints tying `feature_flags.scope` to its reference columns; trace-table FK posture changed to **RESTRICT** (`agent_executions`, `evidence`, `llm_interactions` → deleting a run can no longer cascade away or orphan its audit trail).

## 3. Defects found and fixed during verification

1. **SQLAlchemy `text()` does not tokenize `:param::type`** — a bind immediately followed by a `::` cast is left as literal SQL (Postgres syntax error). Rewritten as `CAST(:param AS type)` throughout the hybrid SQL.
2. **UUID params must pass as `uuid.UUID`** — string org IDs infer as `text` under asyncpg and break `uuid = text` comparisons.
3. **`SET LOCAL` takes no bind parameters** — tenant GUC pinning uses `set_config(name, value, is_local ⇒ true)` instead.

## 4. Notes and deferrals

- The admin/owner engine (compose superuser) intentionally bypasses RLS for migrations, login (deliberately cross-org, ADR-0013), and seeds; **Phase 4** switches authenticated request paths onto `tenant_session` when business endpoints land.
- Benchmark corpus uses random unit vectors + templated finance prose (clearly synthetic); **real embeddings** arrive with the Phase-4 ingestion pipeline (fastembed per ADR-0007) — the retrieval SQL is the production artifact, the corpus is not.
- Production deployments must supply their own app-role credentials via `IC_DATABASE__RLS_URL` (dev creds are compose-local; documented in `rls.py` and `.env.example`).

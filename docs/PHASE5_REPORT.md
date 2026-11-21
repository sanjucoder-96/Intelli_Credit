# Phase 5 Completion Report — AI Agents & Durable Workflow Engine

**Date:** 2026-08-21 · **Status:** ✅ complete — the durable workflow engine and the full `credit_appraisal` workflow run end to end over a Postgres-checkpointed LangGraph, verified by executable tests against live Postgres/Redis: the happy path (analyse → HITL → resume → deterministic scoring → decision → CAM) and failure/resume (worker death → resume from checkpoint, no re-execution, no duplicate persistence).

## 0. Acceptance criteria — verification evidence

| Criterion | Evidence |
|---|---|
| Config-driven graph construction (conditional edges, Send fan-out/fan-in, interrupt/HITL) | `app/workflows/compiler.py` v2; `tests/test_compiler_v2.py` (9) |
| Durable state (Postgres checkpoints), lifecycle persistence, resume | `checkpointer.py` + `persistence.py` + `runner.py`; `tests/test_workflow_durable.py` (2) |
| HITL genuinely pauses and resumes; officer input re-scores | `runner.resume` + scratch-merge reducer; `tests/test_credit_appraisal.py` |
| Deterministic scorecard / limit / rate — **LLM never authors a number** | `app/analysis/scorecard.py`; `app/agents/credit.py` `RiskAssessmentAgent`; `tests/test_analysis.py` (9) |
| Explainable decision + CAM IR + CAM generation | `app/analysis/cam.py` (IR + deterministic render, LLM fills only the exec-summary slot) |
| Celery owns execution; worker restart is safe | `app/tasks/workflows.py`; `tests/test_workflow_celery.py` (1) |
| HTTP surface: start / status / HITL submit / report | `app/api/routers/workflows.py`; `tests/test_workflow_api.py` (5) |

**Totals:** 128 tests pass (unit + integration), `mypy` clean on 128 files, `ruff` clean, `ruff format` clean, 2 import-linter contracts kept.

## 1. What was built

**Durable engine (`app/workflows/`)**
- `checkpointer.py` — `AsyncPostgresSaver` (via `psycopg[binary]`), DSN normalised from `settings.database.url`, one-shot idempotent `setup()`.
- `persistence.py` — immutable `workflows` rows (checksum) + `workflow_executions` lifecycle (PENDING→RUNNING→PAUSED_HITL→COMPLETED/FAILED), `thread_id = run_id` correlating to `agent_executions`.
- `runtime.py` — `build_runtime_bundle` (LLMGateway honouring LIVE/RECORD/REPLAY + a ledger writing `llm_calls` tagged with the execution, storage, research) and `make_context_factory` (per-node `AgentContext`: `StructuredCaller`, whitelisted tool belt, `CostGovernor`, `DbTraceWriter`).
- `runner.py` — `WorkflowRunner.start/resume` over the checkpointer; a `context_factory_builder(execution_id)` seam resolves the chicken-and-egg (the context needs the execution id it is about to create).
- `bootstrap.py` — registers agents + built-in definitions once per process (app startup and worker).

**Deterministic core (`app/analysis/`, its own architecture layer above infra/docintel)**
- `ratios.py`, `scorecard.py` (ADR-0009: `S = Σ_p W_p Σ_f w_f n_f(x_f)`, coverage, overlays — knockout / fraud-cap / qualitative-notch ±12 / coverage<0.7→REFER — IC1–IC10 grades, MPBF Tandon II / EBITDA-multiple / turnover limit, `rate = base + grade premium + adjustments`), `five_cs.py`, `cam.py`.

**Agents (`app/agents/credit.py`)** — deterministic `financial_analysis` / `fraud_detection` / `risk_assessment` (persists `risk_assessments` + `score_breakdowns`); LLM `research` (records a gap offline), `recommendation` (narrative only), `cam_generator` (re-derives every number deterministically; the LLM writes only the executive-summary slot).

**Celery ownership (`app/tasks/workflows.py`)** — `workflow.run` / `workflow.resume`, `acks_late` + `reject_on_worker_lost`, deterministic task ids, routed to the `agents` queue.

**HTTP (`app/api/routers/workflows.py`)** — `POST /cases/{id}/runs`, `GET /runs/{id}`, `POST /runs/{id}/hitl`, `GET /runs/{id}/report`, permission-gated (RUN_START / HITL_REVIEW / REPORT_READ), reads on the RLS session.

## 2. Invariants held

Explainability is structural (CAM IR carries the numbers; the renderer prints them verbatim). Evidence is traceable (`agent_executions` per step, `llm_calls` per physical call, both correlated to the execution). **Quantitative decisions come only from the deterministic scorecard/policy grid — the LLM never invents a score, limit, rate, or decision.** State is checkpointable/resumable; HITL genuinely pauses and resumes; secrets never enter traces/logs (the ledger stores model/tokens/cost + a purpose hash, never the prompt or key); local/offline dev works through REPLAY + scripted callers. Phases 1–4 untouched; the platform stays config-driven.

## 3. Hard-won specifics (design decisions worth keeping)

- **Exactly-once is a property of the checkpoint, not of delivery.** A `workflow.run` redelivered by `acks_late` resumes from the checkpoint instead of re-applying the initial input (which the reducer channels `findings`/`cost_usd` would otherwise double-count). **But** a redelivery must not step past a human decision point: if the pending node is an interrupt gate, the run is reported paused *without advancing*. Only an explicit `resume()` / HITL submission advances past the gate.
- **Fan-in HITL resume is ambiguous to LangGraph** — `aupdate_state` at a gate fed by three parallel predecessors requires `as_node`; the runner injects officer input as the interrupted gate node.
- **JSONB persistence** must dump pydantic models with `mode="json"` (`LimitResult.amount` is a `Decimal` — a plain dump is not JSON-serialisable).
- **Windows/psycopg**: the async Postgres saver requires a `SelectorEventLoop`; conftest sets `WindowsSelectorEventLoopPolicy` and the Celery task builds a selector loop. Linux (epoll) is unaffected; asyncpg works on either.
- The `scratch` state channel has a merge reducer so injecting `officer_notes` at resume does not clobber the run-start financials.

## 4. LIVE / RECORD / REPLAY

All three modes remain wired (`settings.agent.llm_mode` → gateway; Redis replay store when redis is present). A controlled LIVE Gemini smoke of the gateway was validated earlier this session — real tokens/cost + structured output, and RECORD→REPLAY byte-identical — on the current models (`gemini-3.7-flash` / `gemini-3.1-pro-preview`; the 2.5 line is retired/404). The new Phase-5 tests are deterministic and offline **by design** (scripted caller, no key), preserving the CI/offline seam; they do not replace the LIVE path.

## 5. Technical debt / remaining

- The workflow engine writes through the **admin** session (agents tag `org_id` explicitly) rather than the RLS session — tighten to the tenant session in Phase 6.
- **Financials assembly** (Phase-4 extracted fields → `scratch.financials`) is currently caller-supplied; auto-wiring from the case's extracted statements is outstanding.
- The `ml_default_risk` pillar is excluded (coverage ≈ 0.80) until the Phase-6 PD model lands.
- Integration tests need the `intellicredit_test` database (CI provides it; locally `CREATE DATABASE intellicredit_test` + `alembic upgrade head`). Run with `IC_INTEGRATION_TESTS=1 pytest -m integration`.
- Nothing committed to git yet (per standing instruction — awaiting the user's go-ahead).

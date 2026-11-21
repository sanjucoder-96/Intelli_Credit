# Phase 2 Completion Report — Folder Structure & Project Skeleton

**Date:** 2026-07-17 · **Status:** ✅ complete, all acceptance criteria verified live

## 1. Acceptance criteria — verification evidence

| Criterion | Evidence |
|---|---|
| Compiles / every import resolves | `test_imports.py` walks and imports **every module** in `app` (69 source files); `python -m compileall` clean |
| Runs with Docker Compose | 6 containers up from one `docker compose up`: postgres (healthy), redis (healthy), api (healthy), worker, beat, frontend |
| Passes CI gates | ruff check + format ✅ · `mypy --strict` ✅ (69 files) · import-linter **2 contracts KEPT** (layers + core-is-shared-kernel) · pytest **58 passed** (unit, zero services) + **1 integration** (migration round-trip) |
| Health endpoints | `GET /api/v1/health` → `{"status":"ok",...}` · `GET /api/v1/ready` → per-dependency checks, 503 envelope when down · `GET /metrics` → Prometheus exposition |
| PostgreSQL connectivity | readiness ping + integration suite against live `pgvector/pgvector:pg17` |
| Redis connectivity | readiness ping + Celery broker/result round-trip |
| Alembic migrations | `0001_initial_schema` applies on api start; integration test proves **upgrade → 17 tables + vector extension → downgrade → clean → re-upgrade** |
| Complete folder structure | see [FOLDERS.md](FOLDERS.md); layering machine-enforced |
| Domain models scaffolded | all 16 requested models + `Organization` (tenancy root) = **17 tables**, UUID PKs, timestamps, soft-delete where appropriate, indexes |
| Dependency injection | `app/api/providers.py` composition root; every test overrides providers (never internals) |
| Configuration management | nested pydantic-settings (`IC_*`/`__`), fail-fast with named missing variables; dev/test/prod via `IC_APP__ENV` |
| Logging | structlog JSON, request-ID middleware, contextvar correlation across API and Celery |
| AuthN skeleton | argon2id hashing + JWT access/refresh (`/auth/login`, `/auth/refresh`, `/users/me`); refresh-as-access rejected; inactive users rejected |
| AuthZ skeleton | `Role`/`Permission` enums, `ROLE_PERMISSIONS`, `require_roles`/`require_permissions` dependencies (403 envelope), `TenantContext` scoping through **one repository choke point** + tested |
| Background worker skeleton | Celery app (queues `agents`/`ingest`/`ocr`, acks_late, explicit `task_queues`), beat schedule, `platform.heartbeat` **proven end-to-end** (send → worker → result) |
| LangGraph runtime skeleton | `WorkflowDefinition` DSL + YAML loader + versioned/checksummed registry + `GraphCompiler` (the only LangGraph-touching module) + `ExecutionRegistry`; compiled no-op workflows execute in tests (sequential, parallel, degraded-parking, required-failure) |
| Testing infrastructure | pytest + pytest-asyncio, ASGI client fixtures, fake ports, DB-session guard rail, integration markers with env-gated skip, fixture workflow YAML |
| No business logic / no fake AI | reviewer-verified: NoopAgent does and claims nothing; zero TODO/FIXME/XXX in the repo |

## 2. What was built (150 files)

**Backend (81 files)** — `backend/app/`: `core/` (config, logging, context, errors, security, first-party metrics) · `domain/` (enums incl. Role/Permission; CIN/GSTIN/PAN/IFSC value objects; read-model entities) · `application/` (ports + auth/users use cases) · `infrastructure/` (async engine, Base + UUID/Timestamp/SoftDelete/Tenant mixins, **17 ORM models**, scoped repositories, async Alembic env + hand-written initial migration, Redis client) · `agents/` (AgentSpec/policies/budgets, BaseAgent lifecycle `plan→act→reflect→revise-once→score`, wgm-v1 confidence algebra, trace events, registry + NoopAgent) · `workflows/` (RunState, definition DSL with DAG validation, loader, immutable-version registry, GraphCompiler, executions) · `api/` (providers DI root, request-context middleware, error envelope handlers, health/auth/users routers, schemas) · `tasks/` (Celery app + heartbeat) — plus `tests/` (12 files, 58 unit + 1 integration test) and `pyproject.toml` (ruff/mypy-strict/pytest/import-linter config).

**Frontend (17 files)** — Next.js 15 App Router + TypeScript strict + Tailwind v4 + TanStack Query v5 + Zustand + shadcn-style ui primitives + next-themes; platform-status board polling `/health` + `/ready` through a typed API client; builds clean (`npm run build`).

**Ops (10 files)** — `docker-compose.yml` (6 services + `--profile ops`: pgAdmin, Flower), multi-stage non-root Dockerfiles (dependency-layer caching), GitHub Actions CI (backend gates → integration with pg17+pgvector/redis services → frontend lint+build → docker builds), Makefile, `.env.example`, `.gitignore`, `.pre-commit-config.yaml`, `.dockerignore`.

**ML stub + data governance (7 files)** — `ml/` Typer CLI package (pipelines land in Phase 6); `data/real/` vs `data/synthetic/` trees with the ADR-0010 separation rule documented.

**Docs** — [DEVELOPMENT.md](DEVELOPMENT.md), [FOLDERS.md](FOLDERS.md), this report; ADR-0012 amended (metrics pivot).

## 3. Packages added

**Backend runtime:** fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, alembic, pgvector, redis, celery, langgraph, langgraph-checkpoint-postgres, pydantic[email], pydantic-settings, structlog, PyJWT, argon2-cffi, tenacity, prometheus-client, PyYAML, httpx, python-multipart.
**Backend dev:** pytest, pytest-asyncio, ruff, mypy, import-linter, types-PyYAML.
**Frontend:** next 15, react 19, @tanstack/react-query 5, zustand 5, tailwindcss 4 (+@tailwindcss/postcss), next-themes, class-variance-authority, clsx, tailwind-merge, lucide-react, @radix-ui/react-slot; dev: typescript 5, eslint 9 + eslint-config-next.
**ML stub:** typer.

## 4. Deviations from plan (all forced, all documented)

1. **`prometheus-fastapi-instrumentator` dropped** — incompatible with FastAPI ≥ 0.139 (router entries without `.path` crash its route-name lookup). Replaced with a first-party ~40-line middleware on `prometheus-client` (`app/core/metrics.py`); ADR-0012 amended. *Known nit:* under FastAPI 0.139's include-router mechanism the matched route template omits the API prefix in metric labels (`/health` not `/api/v1/health`) — bounded cardinality, cosmetic, revisit in Phase 4.
2. **`ORJSONResponse` dropped** — deprecated by current FastAPI (pydantic-native serialization); removed along with the `orjson` dependency.
3. **Celery beat schedule file** → `-s /tmp/celerybeat-schedule` (non-root container can't write the workdir).
4. **Model naming** follows the product owner's Phase-2 list; docstrings map each to its ARCHITECTURE concept (`LoanApplication`=Case, `WorkflowExecution`=WorkflowRun, `AgentExecution`=AgentStep, `LLMInteraction`=llm_calls). `Organization` was added as the structural tenancy root (ADR-0013 requires org scoping; the model list had no tenant table).

## 5. Architectural consistency review — outcome

Independent reviewer (read-only) confirmed: layering clean (langgraph imports confined to the compiler; grep-verified), scope discipline clean, forbidden dependencies absent, migration↔ORM parity column-by-column, cross-file consistency (compose/CI/env/docs). **No critical findings.** 4 major + 12 minor findings; **all 4 majors and 10 minors fixed and re-verified** in this phase:

- default confidence weights now include `validators` (a failing validator suite must sink the score — Phase-5-proofing)
- tenant scoping refactored through **one** `BaseRepository.scoped()` choke point (login paths route through it with `ctx=None`)
- integration tests env-gated (`IC_INTEGRATION_TESTS`); `make test` = unit only, `make test-integration` added; CI sets the gate
- Celery queues declared explicitly from config (kombu `Queue`s)
- plus: feature-flag accessor made multi-row-safe, langchain-core import made typing-only, dead deps removed (aiosqlite/orjson), core-is-shared-kernel import-linter contract added, RLS phase references corrected, Dockerfile dependency-layer caching, docs corrections.

Deferred to Phase 3 (advisory, recorded): FK indexes for obvious query paths (`risk_assessments/llm_interactions/reports.workflow_execution_id`, `loan_applications.company_id`, evidence FKs), CHECK constraints tying flag `scope` to its reference columns, ondelete posture review for trace tables (CASCADE→RESTRICT), `ComponentLedger` seam when real components exist.

## 6. Known boundaries (by design, per phase scope)

No business endpoints, no document ingestion, no RAG, no LLM calls, no ML, no scoring, no reports — those are Phases 3–7 per [ROADMAP.md](ROADMAP.md). `workflows/definitions/` is deliberately empty until Phase 5. The repo is `git init`-ed with everything untracked — **no commits were made** (say the word and I'll create the initial commit).

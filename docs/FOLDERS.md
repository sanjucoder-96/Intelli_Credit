# Repository layout

```
IntelliCredit/
├── backend/                    # FastAPI + Celery + LangGraph modular monolith (ADR-0001)
│   ├── pyproject.toml          # deps, ruff, mypy --strict, pytest, import-linter contract
│   ├── alembic.ini
│   ├── app/
│   │   ├── main.py             # app factory: logging → middleware → error handlers → routers → metrics
│   │   ├── core/               # shared kernel (importable by every layer)
│   │   │   ├── config.py       #   pydantic-settings (IC_* env), fail-fast validation
│   │   │   ├── logging.py      #   structlog JSON + contextvar correlation ids
│   │   │   ├── context.py      #   TenantContext, request-id contextvar
│   │   │   ├── errors.py       #   AppError hierarchy → HTTP error envelope
│   │   │   ├── security.py     #   argon2 hashing, JWT issue/verify (ADR-0013)
│   │   │   └── metrics.py      #   first-party Prometheus middleware + /metrics
│   │   ├── domain/             # pure layer: enums, CIN/GSTIN/PAN/IFSC value objects, read models
│   │   ├── application/        # use cases + ports (Protocols) — no I/O
│   │   ├── infrastructure/     # adapters
│   │   │   ├── db/             #   async engine, Base + mixins, 17 ORM models,
│   │   │   │                   #   TenantContext-scoped repositories, Alembic migrations
│   │   │   └── redis/          #   async Redis client + readiness ping
│   │   ├── agents/             # BaseAgent lifecycle (plan/act/reflect/revise/score),
│   │   │                       # AgentRegistry, budgets, trace events, confidence algebra
│   │   ├── workflows/          # workflows-as-data (ADR-0008): definition DSL, YAML loader,
│   │   │                       # versioned registry, GraphCompiler (sole LangGraph surface),
│   │   │                       # execution registry; definitions/ stays empty until Phase 5
│   │   ├── api/                # routers, schemas, DI composition root (providers.py),
│   │   │                       # request-context middleware, error handlers
│   │   └── tasks/              # Celery app (queues: agents/ingest/ocr) + heartbeat task
│   └── tests/                  # unit suite (no services) + integration marks; fixtures/
├── frontend/                   # Next.js 15 App Router, TS strict, Tailwind v4, TanStack Query,
│                               # Zustand, shadcn-style ui, next-themes; status page hits /health
├── ml/                         # Typer CLI stub — real pipelines land in Phase 6 (ADR-0010)
├── data/
│   ├── real/                   # REAL_PUBLIC datasets only (training-eligible)
│   └── synthetic/              # SYNTHETIC_DEMO only — never reaches training (CI-guarded rule)
├── deploy/                     # backend.Dockerfile, frontend.Dockerfile (multi-stage, non-root)
├── docs/                       # ARCHITECTURE.md, adr/ (16 records), ROADMAP.md, RISK_REGISTER.md,
│                               # DEVELOPMENT.md, FOLDERS.md, PHASE2_REPORT.md
├── .github/workflows/ci.yml   # lint → typecheck → layers → tests (+services) → builds
├── docker-compose.yml          # postgres(pgvector:pg17), redis, api, worker, beat, frontend
│                               # --profile ops: pgadmin, flower
├── Makefile                    # up/down/migrate/test/lint/fmt (demo-seed arrives Phase 9)
└── .env.example                # annotated IC_* configuration reference
```

**Layering rule (machine-enforced):** `domain ← application ← (infrastructure | agents | workflows | api | tasks)`, with `app/core` as the shared kernel — see the import-linter contract in `backend/pyproject.toml` and [ADR-0001](adr/ADR-0001-modular-monolith.md).

**Model naming:** the Phase-2 tables use the product owner's names; docstrings map them to the ARCHITECTURE concepts they implement (`LoanApplication` = Case, `WorkflowExecution` = WorkflowRun, `AgentExecution` = AgentStep/trace, `LLMInteraction` = llm_calls ledger).

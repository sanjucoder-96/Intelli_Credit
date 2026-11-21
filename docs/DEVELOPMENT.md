# Development guide

## Prerequisites

- **Docker Desktop** (Linux containers) — the entire stack runs via compose.
- **Python 3.12+** and **Node 22+** only if you want to run backend/frontend natively during development.
- Windows, macOS, and Linux all work; the repo is developed on Windows 11 (Git Bash / WSL2 for `make`).

## Quickstart (everything in Docker)

```bash
cp .env.example .env          # defaults work for local compose
docker compose up -d          # postgres+pgvector, redis, api (runs migrations), worker, beat, frontend
```

| Surface | URL |
|---|---|
| API | http://localhost:8000 · docs at `/docs` |
| Liveness / readiness | `GET /api/v1/health` · `GET /api/v1/ready` |
| Prometheus metrics | `GET /metrics` |
| Frontend | http://localhost:3000 |
| pgAdmin / Flower | `docker compose --profile ops up -d` |

The `api` container runs `alembic upgrade head` before serving, so a fresh
clone reaches a fully migrated schema with one command.

## Native backend loop (fast iteration)

```bash
cd backend
python -m venv .venv && ./.venv/Scripts/activate   # source .venv/bin/activate on POSIX
pip install -e ".[dev]"
docker compose up -d postgres redis                # state stays in Docker
uvicorn app.main:app --reload                      # reads .env (IC_* variables)
```

Environment variables use the `IC_` prefix with `__` as the nesting delimiter
(`IC_DATABASE__URL`, `IC_AUTH__JWT_SECRET`, …) — see `.env.example` for the
full annotated list. Configuration is validated fail-fast at boot
([app/core/config.py](../backend/app/core/config.py)).

> Settings read `.env` from the **current working directory**. The repo's
> `.env` lives at the root, so for the native loop either copy it
> (`cp ../.env .env` inside `backend/`) or export the `IC_*` variables in
> your shell. Inside Docker this is handled by `env_file` in compose.

## Quality gates (all must pass; CI runs exactly these)

```bash
cd backend
ruff check app tests && ruff format --check app tests   # lint + formatting
mypy app                                                # strict typing
lint-imports                                            # Clean Architecture layers (ADR-0001)
pytest -m "not integration"                             # unit suite — no services needed
pytest -m integration                                   # needs live postgres/redis + IC_* env
```

Frontend: `cd frontend && npm run lint && npm run build`.

## Migrations (Alembic)

```bash
cd backend
alembic upgrade head                     # apply
alembic downgrade -1                     # step back
alembic revision -m "add_chunks_table"   # new revision (autogenerate against Base.metadata)
```

Migration environment: [app/infrastructure/db/migrations/env.py](../backend/app/infrastructure/db/migrations/env.py)
(async engine; reads `IC_DATABASE__URL`, overridable via `ALEMBIC_DATABASE_URL`).

## Background workers

```bash
celery -A app.tasks.celery_app worker -Q agents,ingest,ocr --pool=threads -l info
celery -A app.tasks.celery_app beat -l info
```

Queues follow ADR-0004 (`agents` / `ingest` / `ocr`; ML rides `ocr`). The
`platform.heartbeat` task (every 5 min via beat) proves the wiring end-to-end.

## Conventions that CI enforces

- **Layering:** `domain ← application ← (infrastructure | agents | workflows | api | tasks)` — machine-checked by import-linter; `app/core` is the shared kernel.
- **Typing:** `mypy --strict`; third-party escape hatches live only in `pyproject.toml` overrides.
- **No TODO/FIXME markers** — seams for later phases are documented in module docstrings ("Phase 5 adds …").
- **Tenancy:** every repository call takes a `TenantContext`; org scoping at one choke point (ADR-0013).
- **The GraphCompiler rule:** `app/workflows/compiler.py` is the only module that may import LangGraph builder APIs (ADR-0008).

# IntelliCredit — Deployment Guide

One `docker compose up` produces a complete, migrated, tenant-ready IntelliCredit
environment. This guide covers local (Docker), the required environment, a
production-like single-VM layout, and which components need persistent storage.

The project invariant holds: **no Kubernetes, no microservices, no external
cloud dependency is required** — the whole system runs from one compose file.

---

## 1. Local — `docker compose up`

```bash
cp .env.example .env          # once — compose interpolates ${...} from it
docker compose up -d --build  # core stack; the `migrate` service runs first
make seed                     # demo tenant + logins (see below)
```

What comes up (default profile):

| Service | Purpose | Port | Healthcheck |
|---|---|---|---|
| `postgres` | PostgreSQL 17 + pgvector (records, vectors, checkpoints, ledger) | 5432 | `pg_isready` |
| `redis` | broker / results / cache (append-only, persisted) | 6379 | `redis-cli ping` |
| `migrate` | one-shot: `alembic upgrade head` + ensure the RLS app role, then exits | — | (runs to completion) |
| `api` | FastAPI (uvicorn) | 8000 | `curl /api/v1/health` (image) |
| `worker` | Celery worker (`agents,ingest,ocr`) | — | `celery inspect ping` |
| `beat` | Celery beat scheduler | — | (process liveness) |
| `frontend` | Next.js standalone production server | 3000 | `wget /login` |

Startup ordering is enforced by `depends_on` conditions: `api`, `worker` and
`beat` all wait for `postgres`/`redis` **healthy** AND for `migrate` to
**complete successfully**, so no service ever touches an unmigrated schema or a
missing tenant role on a cold boot. `frontend` waits for `api` healthy.

Ops extras (`docker compose --profile ops up -d`): `pgadmin` (:5050),
`flower` (:5555).

Surfaces once up:
- Frontend: <http://localhost:3000> — log in as `analyst@ / officer@ / admin@demo.intellicredit.in`, password `Demo!Pass123` (after `make seed`).
- API docs: <http://localhost:8000/docs> · health: `/api/v1/health` (liveness), `/api/v1/ready` (readiness — pings PG + Redis) · metrics: `/metrics` (Prometheus).

Restart / rebuild helpers: `make up` / `make down` / `make logs` / `make migrate` / `make seed`.

---

## 2. Environment

All backend settings are nested pydantic-settings — prefix `IC_`, nested
delimiter `__`. `.env.example` documents every knob; the required ones (no
default, fail-fast at boot) are:

| Variable | Meaning |
|---|---|
| `IC_DATABASE__URL` | async Postgres DSN (admin role) |
| `IC_REDIS__URL` | Redis URL |
| `IC_AUTH__JWT_SECRET` | ≥16 chars — **rotate for production** |
| `IC_CELERY__BROKER_URL` / `IC_CELERY__RESULT_BACKEND` | Redis DSNs |

Under `docker compose`, the connection URLs are overridden to the in-network
service hostnames (`postgres`/`redis`) by the `x-backend-env` anchor — the
`.env` 127.0.0.1 values are for running the backend on the host instead.

Optional but relevant:
- `IC_DATABASE__RLS_URL` — the non-superuser RLS role DSN (tenant traffic). Created by the `migrate` service.
- `IC_GEMINI__API_KEY` (or `GEMINI_API_KEY` / `GOOGLE_API_KEY` / `GEMINI_API`) — enables live LLM steps; without it, LLM-dependent steps degrade honestly.
- `IC_TAVILY__API_KEY` — enables live web research; without it the research agent degrades honestly.
- `IC_AGENT__LLM_MODE` = `live` | `record` | `replay` — `replay` runs the demo fully offline from recorded fixtures.
- `IC_ML__ARTIFACT_DIR` — champion PD model dir (compose points it at the shared volume).
- `IC_APP__CORS_ORIGINS` — JSON list of allowed browser origins (add your prod domain).
- `NEXT_PUBLIC_API_URL` — build-time URL the browser uses to reach the API.

**Secrets** are supplied via env only. They never reach the client bundle
(only `NEXT_PUBLIC_*` is inlined), never appear in logs (DSNs are
password-redacted at startup), and never enter traces (recursive redaction of
`api_key`/`password`/`token`/… at any depth). Do not commit a real `.env`
(`.env` is gitignored).

---

## 3. Production-like (single VM)

The same compose file runs on one VM. Recommended shape:

```
            ┌────────── reverse proxy (Caddy/nginx, TLS) ──────────┐
   Internet │  api.example.com → api:8000   app.example.com → frontend:3000
            └───────────────────────────────────────────────────────┘
                    │                         │
      ┌─────────────┴──────────┐   ┌──────────┴─────────┐
      │ api (1+)  worker (1+)  │   │ frontend (Next SSR)│
      │ beat (exactly 1)       │   └────────────────────┘
      └──────────┬─────────────┘
        postgres (1) ── redis (1)
```

- Put a reverse proxy (Caddy is simplest — automatic TLS) in front; terminate
  TLS there and route `api.*` → `api:8000`, `app.*` → `frontend:3000`.
- Set `IC_APP__CORS_ORIGINS` to the frontend's public origin and build the
  frontend image with `NEXT_PUBLIC_API_URL=https://api.example.com`.
- Rotate `IC_AUTH__JWT_SECRET`, `POSTGRES_PASSWORD`, and the RLS role password.
- Scale `api` and `worker` horizontally (both are stateless); keep **exactly one
  `beat`** (a single scheduler). All share one Postgres and one Redis.
- `restart: unless-stopped` is set on every service, so a crashed container
  comes back automatically.

This is deliberately a single-VM design. Multi-host/K8s is explicitly **not**
required and not provided — the modular monolith + 2 stateful services is the
architecture (ADR-0001).

---

## 4. Persistent storage (what must survive a restart)

| Data | Volume | Notes |
|---|---|---|
| Database (cases, evidence, workflow checkpoints, agent traces, decisions, CAM) | `pgdata` → `postgres:/var/lib/postgresql/data` | **Authoritative durable state.** |
| Uploaded documents + derived artifacts + page images | `appstorage` → `api,worker:/srv/storage` | Shared api↔worker (ADR-0016). |
| ML champion model artifacts | `appstorage:/srv/storage/models` (`IC_ML__ARTIFACT_DIR`) | On the shared volume — survives recreation. |
| fastembed model cache | `appstorage:/srv/storage/.fastembed` | Avoids re-download. |
| Redis broker/results/cache | `redisdata` → `redis:/data` (appendonly) | Paused-HITL work + LLM cache survive a redis restart. |

Because every authoritative number lives in Postgres and the workflow state
lives in the durable LangGraph checkpoint, a container restart never rolls a run
back: a killed worker resumes from its checkpoint and completes without
duplicating decisions (proven by `scripts/chaos_demo.py`).

### Backup / restore (single Postgres)

```bash
# Backup
docker exec intellicredit-postgres-1 pg_dump -U intellicredit intellicredit \
  | gzip > backup-$(date +%F).sql.gz
# Restore (into a fresh DB)
gunzip -c backup-YYYY-MM-DD.sql.gz \
  | docker exec -i intellicredit-postgres-1 psql -U intellicredit intellicredit
```

The blob volume (`appstorage`) is backed up with `docker run --rm -v
intellicredit_appstorage:/data -v "$PWD":/backup alpine tar czf
/backup/storage.tgz -C /data .` and restored symmetrically.

---

## 5. Observability

`/metrics` (Prometheus text) exposes: HTTP request rate/latency; workflow
duration + success/failure; agent-node duration; LLM latency/tokens/cost/errors
and execution mode (live/replay/cache/fallback); ingestion-stage duration and
document-processing failures; ML inference duration; CAM generation duration;
Celery task duration, queue latency and outcomes; and DB statement duration by
operation. Correlation IDs (tenant / case / run-id / node) are carried in the
structured JSON logs (and the `agent_executions` / `llm_interactions` ledger
tables), never as high-cardinality Prometheus labels. No secret is emitted.

Point a Prometheus scrape at `api:8000/metrics`; a Grafana dashboard is optional
and not required for the demo.

# IntelliCredit — Demo Setup

Exact, reproducible steps to stand up the demo. Everything here is drawn from the
repo (`Makefile`, `docker-compose.yml`, `backend/app/seeds/demo.py`,
`backend/scripts/`). All demo data is clearly labeled `SYNTHETIC_DEMO`; company
identifiers are format-valid but fictional. No real secrets appear in this file.

---

## 1 · Prerequisites

- **Docker Desktop** (Linux containers), ~6 GB RAM free.
- ~5 GB disk for images + volumes.
- **No API keys required** for the offline demo. Gemini/Tavily are optional; when
  absent, LLM/research steps degrade honestly and the seeded runs use an offline
  scripted caller.
- Optional native dev loop (no Docker): Python 3.12+, Node 22+ — see
  [DEVELOPMENT.md](DEVELOPMENT.md).

Windows note: GNU Make isn't bundled with Windows. Run `make` targets from **Git
Bash** or **WSL**, or run the underlying `docker compose …` command shown beside
each target.

---

## 2 · Environment

One `.env` at the repo root drives the whole stack (compose interpolates `${…}`).

```bash
cp .env.example .env
```

The defaults are demo-ready. Notable `IC_`-prefixed settings you *may* set (never
commit real values):

| Variable | Purpose | Demo default |
|---|---|---|
| `IC_AGENT__LLM_MODE` | `live` \| `record` \| `replay` | `live` (falls back gracefully with no key) |
| `IC_GEMINI__API_KEY` | enables live LLM extraction/narrative | *(unset — offline demo)* |
| `IC_TAVILY__API_KEY` | enables live web research | *(unset — offline demo)* |
| `IC_ML__ARTIFACT_DIR` | persistent model artifacts | `/srv/storage/models` (on a named volume) |

For a **fully offline** demo, set `IC_AGENT__LLM_MODE=replay` (serves only
recorded responses; the run-mode badge shows REPLAY).

---

## 3 · Start the stack

```bash
docker compose up -d --build      # or: make up
```

This starts, in dependency order:

| Service | Role | Port |
|---|---|---|
| `postgres` | pgvector/pgvector:pg17 — the everything-store (volume `pgdata`) | 5432 |
| `redis` | broker, cache, SSE pub/sub (appendonly, volume `redisdata`) | 6379 |
| `migrate` | **one-shot**: `alembic upgrade head` + ensure the RLS role, then exits | — |
| `api` | FastAPI / uvicorn (gates on `migrate` + healthy postgres/redis) | 8000 |
| `worker` | Celery — ingestion, OCR, agent graphs, ML | — |
| `beat` | Celery beat — scheduled jobs | — |
| `frontend` | Next.js standalone (gates on `api` healthy) | 3000 |

Migrations run **automatically** via the one-shot `migrate` service — you do not
run Alembic by hand on a clean boot.

Optional ops profile (`docker compose --profile ops up -d`): pgAdmin `5050`,
Flower `5555`.

Check health:

```bash
docker compose ps
curl http://localhost:8000/api/v1/health     # liveness
curl http://localhost:8000/api/v1/ready      # readiness (pings Postgres + Redis)
```

---

## 4 · Seed the demo tenant

```bash
make seed        # = docker compose exec api python -m app.seeds.demo
```

Idempotent (keyed on slugs/emails). It creates the org, three role users, three
synthetic companies, and two draft cases, and bootstraps the Postgres RLS
application role. It prints the credentials on completion.

> The `make demo-seed` target is a **stub** that errors — use `make seed`.

### Demo accounts

Organization: **`demo-lender`** ("Demo Lender (Synthetic)"). Shared password for
all three users: **`Demo!Pass123`**.

| Email | Name | Role | Can… |
|---|---|---|---|
| `analyst@demo.intellicredit.in` | Asha Analyst | analyst | upload docs, start appraisals |
| `officer@demo.intellicredit.in` | Omar Officer | credit officer | + submit HITL reviews |
| `admin@demo.intellicredit.in` | Arjun Admin | admin | + manage feature flags |

Use the **officer** account to demo the human-review step.

### Demo companies & cases

| Company | Sector | Case | Seeded state |
|---|---|---|---|
| Acme Textiles Private Limited | Textiles | Working-capital appraisal — Acme… | draft |
| Deccan Agro Foods Limited | Food Processing | Working-capital appraisal — Deccan… | draft |
| Nimbus Logistics Private Limited | Logistics | *(no case)* | — |

---

## 5 · Populate real engine output (recommended)

Seeding creates *draft* cases. To get a scored case and a case paused at review —
so the ML / Scorecard / Decision / CAM / Review panels have data — run the real
engine on the seeded cases (offline scripted caller; no keys needed):

```bash
docker compose exec api python -m scripts.provision_demo       # runs credit_appraisal on 2 cases
docker compose exec api python -m scripts.provision_documents  # attaches a doc with evidence bboxes
```

Result:

- **Acme Textiles** → run **completed** on the APPROVE path (strong financials);
  gets a soft finding (GSTR-2B-vs-3B ITC mismatch).
- **Deccan Agro Foods** → left **paused at HITL** (moderate financials + hard
  fraud signals: ~40% GST-vs-bank turnover shortfall (HIGH), 3 cheque returns
  (MEDIUM)) — ready to demonstrate the review → resume → re-score flow.

To finish the paused case from the command line instead of the UI:

```bash
docker compose exec api python -m scripts.resume_offline <run_id>
```

---

## 6 · The exact demo flow

1. Open **http://localhost:3000** → the login page pre-fills demo credentials →
   sign in as the **officer**.
2. **Dashboard** (`/dashboard`) — portfolio snapshot.
3. **Cases** (`/cases`) → open **Acme Textiles**.
4. In the case workspace (`/cases/{id}`) click through the tabs:
   **Documents** → **Evidence** (click a "region" source to see the highlighted
   bounding box) → **Financial** → **Research** → **Fraud** → **ML Risk** →
   **Scorecard** → **Decision** → **CAM** (→ **Download PDF**).
5. Open **Deccan Agro Foods** → **Review** tab → add an observation + notch →
   **Submit review** → watch it resume and re-score.
6. Durable-resume proof:
   `docker compose exec api python -m scripts.chaos_demo`.

The scripted five-minute narration is in [HACKATHON_DEMO.md](HACKATHON_DEMO.md).

---

## 7 · Reset / teardown

```bash
docker compose restart worker api          # restart compute (data persists in volumes)
docker compose down                        # stop; keep volumes (pgdata/appstorage/redisdata)
docker compose down -v                     # stop + delete all data volumes (full reset)
```

After a `down -v`, re-run steps 3–5 to reseed.

---

## 8 · Environment-limitation note (honest)

Prior phases hit an intermittently-unresponsive host Docker daemon that blocked
the live `docker compose up` smoke. Where a live-stack result is reported in
[FINAL_STATUS.md](FINAL_STATUS.md), it states whether it was actually executed or
is a ready-to-run script — no Docker result is claimed that wasn't run.

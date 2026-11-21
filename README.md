# IntelliCredit AI

**An AI-powered Corporate Intelligence & Credit Decision Platform.**

Banks, NBFCs, insurers, fintechs, auditors, and investment firms upload heterogeneous evidence about a company — GST returns, ITRs, bank statements, annual reports, contracts, legal notices — and IntelliCredit runs an agentic pipeline (*parse → research → analyze → score → decide → report*) producing an explainable, citation-backed decision memo with a transparent quantitative recommendation.

Credit appraisal is workflow #1 of 12 (vendor risk, investment due diligence, ESG, insurance underwriting, compliance, financial health monitoring, fraud detection, corporate intelligence, loan recommendation, business research, portfolio monitoring). The platform's core asset is the mechanism that makes **workflow #13 a configuration exercise** — a new vertical ships as a YAML definition, a document pack, a scorecard config, and a report template, not a code fork.

## Product invariants

1. **Explainability is structural, not prompted** — every claim traces to evidence (page + bounding box, or an archived web snapshot); every score is a printed formula; every agent step is replayable.
2. **Numbers come from deterministic code and calibrated models; words come from the LLM** — a language model can never invent a score, a limit, or an interest rate.
3. **One `docker compose up`** — the entire platform runs locally on a laptop, demoable offline.

## Stack

Python 3.12 · FastAPI · PostgreSQL 17 + pgvector (hybrid RAG: FTS + HNSW + RRF) · Redis · Celery · LangGraph (durable checkpoints, human-in-the-loop interrupts) · Gemini 2.5 Pro/Flash · RapidOCR + Gemini vision escalation · XGBoost / LightGBM / SHAP / Optuna · Next.js 15 · TypeScript · Tailwind · shadcn/ui · Framer Motion · Apache ECharts · Docker Compose

## Project status

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture | ✅ complete |
| 2 | Folder structure & skeleton | ✅ complete — [report](docs/PHASE2_REPORT.md) |
| 3 | Database | ✅ complete — [report](docs/PHASE3_REPORT.md) |
| 4 | Backend APIs & ingestion | ✅ complete — [report](docs/PHASE4_REPORT.md) |
| 5 | AI agents & workflow engine | ⏳ next |
| 6 | ML models | — |
| 7 | Frontend | — |
| 8 | Testing & evals | — |
| 9 | Deployment | — |
| 10 | Documentation | — |

## Quickstart

```bash
cp .env.example .env
docker compose up -d     # postgres+pgvector, redis, api (migrates), worker, beat, frontend
make seed                # demo tenant + users per role + synthetic companies/cases
```

API http://localhost:8000 (`/docs`, `/api/v1/health`, `/metrics`) · Frontend http://localhost:3000 — full guide: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Documentation

- [System architecture](docs/ARCHITECTURE.md) — the accepted Phase-1 baseline
- [Architecture decision records](docs/adr/) — 16 ADRs, including the deliberate simplifications of the prescribed stack
- [Build roadmap](docs/ROADMAP.md) — 10 phases with acceptance criteria
- [Risk register](docs/RISK_REGISTER.md) — panel-surfaced risks, mitigations, open questions
- [Development guide](docs/DEVELOPMENT.md) · [Repository layout](docs/FOLDERS.md) · [Phase 2 report](docs/PHASE2_REPORT.md)

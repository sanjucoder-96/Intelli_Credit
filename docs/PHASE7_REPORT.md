# Phase 7 Completion Report — Frontend + CAM / Report Experience

**Date:** 2026-08-26 · **Status:** ✅ the judge-facing IntelliCredit product experience is built and **verified end-to-end against the real backend** — login → dashboard → case workspace → evidence → analysis → research → risk/fraud → ML risk → scorecard → decision → HITL → CAM. Every quantitative value is served from a backend contract; the frontend never recomputes a score, limit, rate, or PD.

## 0. End-to-end demo (verified in-browser against the live API)

- **Analyst** logs in → **Dashboard** shows the real portfolio (3 active cases, 1 awaiting review, 1 decision; recent decision *Acme Textiles · IC2 · APPROVE · 84.5*).
- **Case workspace** (Acme Textiles) renders the company header + outcome (IC2 / APPROVE / 84.5 / PD 3.5% / ₹1.88 Cr / 10.0% / coverage 100%), the workflow stepper, and all panels:
  - **Scorecard**: pillar rollup (Financial Strength 90.7, ML Default Risk 82.7, …) with expandable per-factor formula → input → normalized → contribution, plus the fired overlays.
  - **ML Risk**: PD 3.5% calibrated (36.8% raw), model `pd_model v1.0.0` (xgboost), the honest "relative signal / not an Indian PD" disclaimer, and exact TreeSHAP reason codes (with a "not available" flag on missing features).
  - **Decision**: APPROVE, limit ₹1,87,50,000 with the MPBF binding-constraint arithmetic, rate build-up, and a structured "why" from persisted factors/overlays.
  - **CAM**: the full memo rendered from the IR — executive summary, recommendation, Five Cs (with drivers), 12 financial ratios, findings, overlays — with Markdown export.
- **Officer** logs in → the paused case (Deccan Agro) opens on the **Review** tab → submits an observation *"Factory at 40% capacity"* (Concern −4) → `POST /runs/{id}/hitl → 202` → the workflow **resumes and re-scores** → the case becomes **IC7 / DECLINE (44.4)**. Human input genuinely shaped the outcome.

## 1. Backend additions (smallest contracts for the screens)

Persisted the ML prediction (PD + calibration + model provenance + TreeSHAP reason codes) into `RiskAssessment.explanation.ml` (previously checkpoint-only), then added four read-only, RLS-scoped projection endpoints (no business logic):

| Endpoint | Serves |
|---|---|
| `GET /cases/{id}/runs` | case → runs with grade/decision/score/PD/limit/rate/report |
| `GET /runs/{id}/detail` | full scorecard breakdown, overlays, limit/pricing, ML prediction, node statuses |
| `GET /cases/{id}/findings` | current findings + evidence ledger (document/page/quote/bbox/confidence) |
| `GET /dashboard/summary` | portfolio counts, recent decisions, grade/decision distribution |

`app/api/schemas/insights.py` + `app/api/routers/insights.py`; **4 integration tests** (`tests/test_insights.py`).

## 2. Frontend (Next.js 15 App Router, React 19, Tailwind 4)

Built on the existing shadcn/new-york + TanStack Query + Zustand skeleton (no architecture replacement).

- **Foundation:** auth-aware API client (Bearer + refresh-on-401), Zustand auth store (login/refresh/hydrate, role gating), risk-state design tokens (added `warning`), 16 shadcn primitives, app shell (sidebar/header/theme), login page, route-group auth gate.
- **Data layer:** `lib/types.ts` (full contract types), `lib/hooks.ts` (react-query with live polling for active runs), `lib/format.ts` (Indian crore/lakh money, ratios, and the grade/decision/severity/PD → tone system).
- **Pages/routes:** `/login`, `/dashboard`, `/cases`, `/cases/[caseId]`.
- **Case workspace components:** company header, workflow-progress stepper, start-appraisal dialog (financials), and nine panels — Overview, Evidence explorer, Financial, Research, Fraud, **ML Risk**, **Scorecard**, **Decision**, **CAM** — plus the **HITL** review panel.
- **Visualisations:** hand-rolled SVG (score gauge, factor bars, bipolar SHAP contribution bars) — on-brand for a credit terminal, no chart-lib bloat.
- Strong empty / loading / error / "unavailable" states throughout; missing values render an em dash, never a fabricated number.

## 3. Invariants preserved

Backend is the source of truth; the frontend transports and renders, never computes a decision. Scores/limits/rates/PD come from backend contracts. Evidence is traceable (findings + evidence ledger with page/quote/region). ML is clearly distinguished from deterministic policy (a dedicated panel, the "relative signal" disclaimer, the model card). HITL is a real workflow interaction (submit → resume → re-score). No secrets in the frontend (JWT in localStorage; keys stay server-side). Replay/offline backend capabilities are untouched.

## 4. Tests & quality gates

- **Frontend:** `tsc --noEmit` clean, `eslint` clean, `next build` succeeds (all 7 routes), **15 Vitest tests** pass (tone system + Indian money formatting; risk-badge, empty/error-state, and score-gauge render tests).
- **Backend:** `mypy` clean (136 files), `ruff` clean, import-linter 2 contracts, **159 passed** (+4 new insights tests). The single failure is the recurring external **Gemini 503** in a Phase-4 extraction test — no Phase-7 code is in its traceback.

## 5. Demo provisioning

`scripts/provision_demo.py` runs the real `credit_appraisal` workflow on the seeded cases (deterministic engine + real champion PD model + offline scripted narrative) and injects forensic findings, producing genuine engine output in the dev DB; `scripts/resume_offline.py` completes a paused run offline when live Gemini is unavailable. Demo credentials: `analyst@` / `officer@demo.intellicredit.in` / `Demo!Pass123`.

## 6. Limitations / remaining gaps

- **Evidence bbox highlighting:** the region data (normalised bboxes) is surfaced with a "region" indicator, but there is no in-browser PDF overlay viewer yet.
- **CAM PDF/DOCX export:** Markdown export works (client blob over the backend-rendered markdown); a PDF/DOCX report renderer + endpoint is a backend follow-up (the IR is renderer-ready).
- **Document upload UI** and the Phase-4 ingestion progress (SSE) are not surfaced; the appraisal is started from entered financials.
- **Live HITL resume** depends on Gemini (the recommendation agent has no offline fallback); the demo completes the resume offline via the scripted caller.
- **Financial trend charts:** single-period statements today, so no multi-year time-series.
- **UI tests** are unit/render-level; no full interaction tests (e.g. mocked-fetch HITL submit) yet.

## 7. Recommended next

Phase 8 (integration/deploy) — wire the frontend Docker image + the report PDF/DOCX renderer endpoint (so CAM export is backend-driven), add the document-upload + ingestion-progress UI, and a small set of interaction/E2E tests (Playwright) over the login → appraisal → HITL → CAM flow.

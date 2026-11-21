# Phase 7 Hardening Report — Demo-Grade Product

**Date:** 2026-08-26
**Scope:** Close the five remaining gaps that stood between the Phase-7 frontend and an
undeniable, self-contained 5–10 minute judge demonstration — without overbuilding.

All work was verified against the **real backend** (live API + Celery worker + Postgres +
Redis), driven end-to-end in a real browser. "Build passes" was never treated as the
finish line; every item was exercised.

---

## 1. Real document-upload experience — **DONE**

A judge can now upload a document and watch it flow **Upload → Ingestion Progress →
Extraction → Evidence → Financial Analysis → Credit Appraisal** against real backend rows.

**Frontend** — `src/components/workspace/panels/documents.tsx` (new):
- Drag-and-drop / file-picker dropzone → `POST /api/v1/cases/{id}/documents` (multipart).
- Per-document **stage pills** (Detect Type · Extract Text Layer · Classify · Extract Domain ·
  Cross Validate) that reflect the live `document_stages` rows; the list polls while any
  document is `processing`.
- An accordion expands each document into its **extracted fields** table (field path, value,
  page).
- `start-appraisal.tsx` shows a "Financials extracted from N documents — Use →" banner that
  pre-fills the appraisal form from the assembled financials.

**Backend** — the ingestion pipeline already existed (Phase 4); we added only the smallest
read-projections in `app/api/routers/insights.py`:
- `GET /cases/{id}/documents/{doc}/fields` → `ExtractedFieldView[]`.
- `GET /cases/{id}/financials` → `FinancialsAssembly`. `_parse_financial_path` maps
  `years.N.<field>` (or bare `<field>`) onto the canonical `FinancialLineItems`, groups by
  fiscal-year index, and returns the year with the most line items plus per-field source
  traces (document, page, confidence).

**Live verification.** A real upload (`POST …/documents` → 201) ran the **full nine-stage
pipeline** on the worker: `detect_type → extract_text_layer → ocr_pages → layout_tables`
completed, **live Gemini classified the file as `bank_statement`**, and although
`extract_domain` failed (`extraction_incomplete: No recognizable header row` — the throwaway
test PDF had no transaction table), the pipeline **degraded gracefully** through
`chunk → embed_index → cross_validate` and the document still reached `READY`. This is the
system honestly reporting an extraction limit instead of fabricating values.

---

## 2. Backend CAM PDF export — **DONE**

**Renderer** — `app/analysis/cam_pdf.py::render_cam_pdf(cam: CamReport) -> bytes`:
- Pure renderer over the **existing CAM intermediate representation** — it never recomputes a
  ratio, score, or limit. It prints what the IR already holds.
- fpdf2 with the core Helvetica font (no bundled TTF); a `_latin1()` sanitiser renders `₹` as
  `Rs. ` so the core font is safe. All 15 CAM sections are emitted (title block,
  recommendation, executive summary, five Cs, financials from `ratios.available_ratios()`,
  findings, score breakdown, overlays & notes, …).

**Endpoint** — `GET /runs/{id}/report/export?format=pdf` in `app/api/routers/workflows.py`
validates the stored report IR (`CamReport.model_validate(report.ir)`), renders, and streams
`application/pdf` as an attachment.

**Frontend** — `panels/cam.tsx` gained a **Download PDF** button (binary fetch via `apiBlob`).

**Tests** — `tests/test_cam_pdf.py` (3): extracts text with pypdfium2 to assert IR values print
**verbatim**, that changing an IR value changes the PDF, and that the renderer imports neither
`evaluate_scorecard` nor `compute_ratios` (guaranteeing it can't invent numbers).

**Live verification.** The export endpoint returns a valid 2-page PDF (all sections present);
the UI button fires the request and receives `200`.

> DOCX was deprioritised behind PDF as instructed; PDF is complete and is the demo path.

---

## 3. Evidence viewer / bounding-box highlighting — **DONE (server-rendered)**

The strongest explainability moment — **Claim → Document → Page → Region → visual highlight** —
now works, and works *robustly*.

**Why not react-pdf.** react-pdf 10.5 / pdfjs-dist 5.4 throws
`TypeError: Object.defineProperty called on non-object` (a webpack ESM-interop failure) under
Next 15 dev. Worse, that crash corrupts the HMR runtime and disables **all** page
interactivity until a clean `.next` rebuild. A fragile PDF engine in the browser is exactly
what a live demo cannot afford.

**The robust design.** Render the page **server-side** and overlay boxes on a plain image:
- `GET /cases/{id}/documents/{doc}/page/{n}.png` → `app/ingestion/pdf.py::render_page_png`
  (pypdfium2 at 150 dpi — the **existing** helper, reused, not rewritten; the `api → ingestion`
  import is already an allowed layer edge).
- `pdf-highlight-viewer.tsx` overlays each normalized `[0,1]` bbox as a **percentage-positioned
  div** on an `<img>`. Because the coordinates are normalized against the page, percentages map
  onto the image exactly — no pdfjs, no dimension math, offline-safe. `document-viewer.tsx`
  fetches the PNG as a blob. **No file imports react-pdf anywhere** (bundle dropped 1156 → 1085
  modules).

**Live verification.** Opening the "Revenue from operations: 100,000,000" evidence renders the
server PNG (1241×1754) with an overlay at `left 54.76% · top 16.84% · width 28.57% · height
2.69%` — **exactly** the recorded bbox. A cropped render confirmed the box lands tightly on the
figure `100,000,000`.

---

## 4. Gemini failure behavior — **DONE**

LIVE Gemini is retained; the workflow and demo are hardened around transient failures without
ever fabricating an LLM answer.

- **Gateway retry** — `app/agents/llm.py`: `LLMGateway.complete()` wraps the structured call in
  a transient-retry loop (retryable codes 408/429/500/502/503/504, exponential backoff base
  0.6 s, max 3). On exhaustion, if a **replay recording exists** for that prompt it is served
  and tagged `served_from_replay_fallback` in the ledger — an explicit, auditable mode, never a
  silent invention. `GeminiUnconfiguredError` and schema `ValidationError` are *not* retried.
  `AgentConfig.llm_max_retries` is threaded through `runtime.py`. Tests:
  `tests/test_llm_gateway.py` (+3: retries-then-succeeds, raises-after-exhausted,
  falls-back-to-replay).
- **Workflow degradation** — `credit_appraisal.yaml` bumped `v2 → v3`; the `recommendation`
  node is now `required: false`, so a sustained LLM outage degrades the *narrative* rather than
  failing the run (numbers already come from the deterministic engine).

**Honest limitation.** A sustained outage on a code path with **no** replay recording still
surfaces the error — by design. During this session Google's API was in a real 503 demand-spike
("This model is currently experiencing high demand"), which is exactly why the one failing test
(below) failed. The live UI demo, by contrast, degraded gracefully (item 1).

---

## 5. Complete demo workflow — **DONE (exercised end-to-end in a browser)**

`scripts/provision_documents.py` attaches a **real** annual-report PDF (fpdf2, recording each
value's normalized bbox during layout) to the completed Acme case, plus `Document(READY)` +
5 `DocumentStage(DONE)` + 13 `ExtractedField` (with bbox) + 2 `Evidence` (document-linked with
bbox) rows — making the document list, extracted-fields, financials assembly and evidence
highlight demo-reliable independent of live OCR/Gemini.

Verified in the browser (real API + worker): **login → case workspace → Documents (5 stages
done + 13 extracted fields) → Evidence (bbox highlight on the source PDF) → CAM (full memo +
PDF download 200) → financials assembly (`available: true`, 12 fields traced to the
document).** The officer's HITL observation genuinely re-scores the backend decision
(Deccan Agro → IC7 / DECLINE), proving human input drives the outcome.

---

## Quality gates

| Gate | Result |
|---|---|
| Backend `pytest` (unit) | **138 passed, 30 skipped, 0 failed** |
| Backend `pytest` (`IC_INTEGRATION_TESTS=1`) | **167 passed, 1 failed** — the failure is the live-Gemini-503 acceptance flake (`test_mixed_bundle_end_to_end`), reproduced twice = external Google demand spike, not a regression |
| Backend `mypy` | clean |
| Backend `ruff check` / `format` | clean |
| Backend `import-linter` | 2 contracts kept |
| Frontend `tsc --noEmit` | clean |
| Frontend `eslint --max-warnings=0` | clean |
| Frontend `vitest run` | **15 passed** |
| Frontend `next build` | **success** (case route 26.8 kB / 193 kB; smaller than pre-hardening — react-pdf removed) |

New tests this pass: `test_cam_pdf.py` (3), `test_insights.py` (+2 upload/financials), and
`test_llm_gateway.py` (+3 resilience).

---

## Remaining / not done (honest)

- **DOCX export** — deprioritised behind PDF as instructed; PDF is complete.
- **Live HITL resume through the Celery worker** still requires Gemini to be reachable *or*
  `scripts/resume_offline <run_id>`; the gateway retry covers transient blips, not a sustained
  outage on a path with no replay recording.
- **The one failing test** is external: Google's Gemini API is currently returning `503
  UNAVAILABLE` ("high demand"). It will pass again when the service recovers; nothing in the
  code changed its behavior.
- **Playwright E2E** — verification was done by driving the real in-app browser this session;
  an automated Playwright suite is still future work.
- **Git** — nothing has been committed (the user has not asked).

## Operational notes for running the demo

- Services: `uvicorn app.main:app --host 127.0.0.1 --port 8000`; Celery
  `celery -A app.tasks.celery_app:celery_app worker --pool=solo -Q agents,ingest,ocr`;
  frontend `npm run dev` (:3000).
- Seed + provision: `python -m app.seeds.demo` → `python -m scripts.provision_demo` →
  `python -m scripts.provision_documents`. Credentials
  `analyst@ / officer@ / admin@demo.intellicredit.in` · `Demo!Pass123`.
- `npm run build` shares `.next` with a running dev server and will 500 it — restart
  `npm run dev` after any production build.

# Phase 4 Completion Report — Backend APIs & Ingestion Pipeline

**Date:** 2026-07-17 · **Status:** ✅ complete — acceptance criteria verified by executable tests against live Postgres/Redis, **plus a full live-stack smoke through the real API + Celery worker**

## 0. Live-stack smoke (docker compose, real everything)

Login as the seeded analyst → create company + case via API → **multipart-upload the mixed bundle** → the worker pipeline (real RapidOCR, real fastembed with the model cached on the storage volume) processes everything: **6/6 documents READY with all 9 stages DONE**, the scanned PDF classified `gstr3b` via OCR, the ZIP fanned out both children. `POST /cases/{id}/search` returns RRF-fused hits — including the scanned document's OCR text as a vector-arm match. Findings in the live DB: `gst_vs_bank_credits` April **40% HIGH** and May **78.9% HIGH (from the OCR-extracted scan)**, `gstr2b_vs_3b_itc_mismatch` **20% HIGH**, `cheque_return_frequency` MEDIUM — 7 evidence rows citing both source documents.

## 1. Acceptance criteria — verification evidence

| Criterion (ROADMAP Phase 4) | Evidence |
|---|---|
| **Mixed bundle → every document READY with typed extractions** | `tests/test_acceptance_pipeline.py::test_mixed_bundle_end_to_end`: born-digital GSTR-3B **+ scanned GSTR-3B (image-only PDF)** + bank-statement XLSX + ZIP (containing a GSTR-2B with a ruled table + a text note) → **6 documents all READY** (ZIP fans out 2 children); classification lands `gstr3b`/`gstr3b`/`bank_statement`/`gstr2b` |
| **Citations carry page/bbox** | `extracted_fields.outward_taxable_value` = ₹1,00,00,000.00 exactly, `page=1`, citation payload persisted, confidence ≥ 0.9 (from arithmetic post-validation, not self-grading) |
| **The OCR path is real** | the scanned fixture triggers per-page `needs_ocr`; **RapidOCR** transcribes it (mean line confidence ~0.88), page PNGs persist for the viewer — and the OCR-extracted numbers are good enough that **the scanned month fires its own GST↔bank validator finding** |
| **Validators emit findings citing BOTH source documents** | `gst_vs_bank_credits` (40% April gap → HIGH), `gstr2b_vs_3b_itc_mismatch` (20% excess ITC claim → HIGH; the brief's named check), `cheque_return_frequency` all fire; the gap finding's evidence rows cite the GSTR-3B **and** the bank statement |
| **A killed worker resumes without duplicate rows** | `test_killed_worker_resumes_without_duplicates`: DONE-stage redelivery no-ops; a checkpoint forced back to RUNNING (simulated crash) re-executes the handler and **converges** — field/chunk counts identical, attempt incremented |
| **OpenAPI → typed FE client** | the FE client is generated from the running API's schema (`openapi-typescript`); new endpoints (companies/cases/documents/search/events/admin-flags) appear in `/docs` and the schema |
| Quality gates | ruff + format ✓ · `mypy --strict` ✓ (107 files) · import-linter 2 contracts ✓ · **75 unit + 9 integration tests** ✓ |

## 2. What was built

**Storage & connectors** — `StoragePort` with the `LocalFilesystemStorage` backend (content-addressed originals, atomic writes, traversal-refusing keys, derived-artifact overwrite semantics), `EmbeddingProviderPort` + `FastembedProvider` (bge-small-en-v1.5, 384-dim, lazy load, ADR-0007), `SourceConnectorPort` (upload is connector #1), and a thin **`GeminiClient`** (native `response_schema` + Pydantic re-validation, optional key with honest `gemini_unconfigured` parking, pricing-table cost estimates → every call lands in `llm_interactions`).

**The ten-stage pipeline** (ARCHITECTURE §7) — checkpointed stage framework over `document_stages` (RUNNING/attempt/error bookkeeping, SSE event publication, chain advancement) + nine handlers: `detect_type` (puremagic; ZIP fan-out with depth/zip-bomb guards; EML body+attachment fan-out; encrypted-PDF gate → `NEEDS_PASSWORD`), `extract_text_layer` (pypdfium2, per-page density — Indian credit files are hybrid PDFs), `ocr_pages` (**RapidOCR primary → Gemini Flash vision escalation** below the confidence threshold, per ADR-0006; page images persisted), `layout_tables` (pdfplumber born-digital grids), `classify` (tier-1 regex anchors → spreadsheet header sniff → tier-2 Gemini constrained to the taxonomy), `extract_domain` (registry-dispatched, delete-then-insert idempotent `extracted_fields` writes + extraction artifact), `chunk` (profile-aware: prose/statement/KV; tables never split mid-row), `embed_index` (embed + deterministic chunk overwrite → document READY), `cross_validate` (case-wide recompute → findings + evidence).

**Document intelligence** (`app/docintel`) — the `DocumentTypeRegistry` (19 types with classifier anchors incl. GSTR-1/2A/2B/3B, bank statement, ITR, financial statements, **CIBIL commercial**, sanction letter, legal notice, board minutes); Indian-format parsing helpers (lakh/crore amounts, parenthesized negatives, statement dates, GSTIN/PAN patterns); **deterministic extractors** for GSTR-3B (labeled-line parse + tax-arithmetic validation), GSTR-2B/2A (supplier-table ITC ledger), bank statements (XLSX/CSV/PDF-table with header synonyms across bank formats, channel classification, cheque-return detection, **running-balance continuity as the confidence source**, account numbers masked at extraction — §15.6); the LLM-backed seam for narrative documents (ITR, balance sheet, P&L, cash flow) bound to typed schemas; **cross-document validators** — GST↔bank credits, **GSTR-2A/2B↔3B ITC mismatch**, ITR↔GST turnover, cheque-return frequency — pure functions emitting severity/category-tagged outcomes with citations into both sources.

**API surface** — companies (create/list with CIN/GSTIN/PAN validation), cases (create/list/get), **multipart upload** (extension allowlist + size caps → StoragePort → pipeline kickoff), document listing with per-stage progress, original-bytes download (viewer source), **password submission** for encrypted PDFs (Redis-held, TTL'd, never persisted), **hybrid search** (`POST /cases/{id}/search` — FTS+vector+RRF scoped to the case), **SSE progress** (`GET /cases/{id}/events` — `document_stages` snapshot rehydrate, then Redis pub/sub live relay, no DB transaction pinned during streaming), admin **feature-flag endpoints** (Redis-cached service with cache-busting writes, ADR-0015), and **audit rows** on login success/failure and every mutation. **Authenticated request paths now run on `tenant_session`** — repository scoping + RLS, two layers end to end (ADR-0013).

## 3. Defects caught by verification (the reason the harness exists)

1. **Derived-artifact writes were silently dropped** — the content-addressed "path exists → skip" optimization is correct for sha-keyed originals but wrong for fixed derived keys (`pages.json.gz` is rewritten after OCR). The scanned document classified as `unclassified` because classify read the pre-OCR artifact. Fix: `put_derived` always atomic-overwrites. *(Found only because the acceptance test runs a genuinely scanned document.)*
2. **`rapidocr-onnxruntime` pinned `>=1.3` matched nothing** — the package froze at 1.2.3 (successor renamed); the pin silently failed the entire dependency resolution. Relaxed with a comment.
3. **Decimal threshold bug** in the cheque-return validator (`1/3*3 = 0.999…`) — multiply before divide.
4. **SSE + RLS session lifetime** — the stream must not pin its tenant transaction; snapshot in a scoped session, then stream.
5. **C: disk exhaustion** during dependency install (pip cache + Docker build cache) — purged 223 cache dirs + 4.2GB build cache; venv lives on D:.
6. **Asyncio Redis singleton × Celery task loops (risk R19, live)** — each Celery task runs `asyncio.run()` on a fresh loop; the process-wide Redis client's locks bind to the first loop, so every later PDF stage died on `redis.get` ("bound to a different event loop") while XLSX/ZIP paths survived (their only Redis use is the error-swallowed event publisher). Fix: `create_redis()` per pipeline invocation, closed with the stage engine; the singleton stays API-only with a documented warning.
7. **OpenCV shared libraries missing in the slim image** — RapidOCR imports `cv2`, which needs `libgl1/libglib2.0-0/libxcb1`; added to the runtime layer (headless-opencv swap noted as a later optimization).
8. **Named-volume ownership vs non-root user** — `/srv/storage` mounted root-owned; the image now pre-creates it owned by the app user so the volume inherits writable ownership on first mount.
9. **Seed emails used a `.local` domain** — pydantic's `EmailStr` rejects special-use domains, so the seeded users could not log in through the API schema. Seeds now use `demo.intellicredit.in`.

## 4. Honest boundaries (documented, not faked)

- **LLM-dependent steps park without a key**: classify tier-2, vision-OCR escalation, and narrative-statement extraction require `IC_GEMINI__API_KEY`; without it they record `gemini_unconfigured` and the pipeline continues (documents stay searchable). Deterministic extraction, OCR, chunking, embedding, search, and validators are fully functional offline.
- **Scanned-table reconstruction via Gemini vision** is a documented Phase-5 seam; Phase 4 parses scanned statutory forms from OCR text lines (proven by the scanned GSTR-3B firing validators).
- Per-invocation engine creation in the Celery path is correct-first; worker-lifetime pooling is a Phase-5 optimization.
- The acceptance test injects a deterministic hash embedder at the port seam (no model download in CI); the real `FastembedProvider` runs in the live stack (model cached on the storage volume via `FASTEMBED_CACHE_PATH`).

## 5. Dependencies added

`puremagic`, `pypdfium2`, `pdfplumber`, `rapidocr-onnxruntime` (1.2.x — final line under this name), `fastembed`, `google-genai`, `openpyxl`; dev: `fpdf2` (fixture generation only). Compose: shared `appstorage` volume mounted on api+worker; `IC_STORAGE__ROOT`, `FASTEMBED_CACHE_PATH`, and `IC_GEMINI__API_KEY` (optional) documented in `.env.example`.

# ADR-0006: OCR: RapidOCR primary, Gemini vision escalation, validator-gated

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

Indian credit files are hostile OCR territory. A single upload routinely mixes typed annexures with scanned, stamped, skewed pages: GSTR-3B printouts, dot-matrix bank statements, notarised certificates with rubber stamps and signatures over the text, photocopied sanction letters. They are also **hybrid PDFs** — born-digital and scanned pages interleaved in one file — so any document-level "OCR or not" decision wastes money on clean pages or garbles dirty ones. The decision must be per page.

The platform's first product invariant raises the stakes beyond raw accuracy: every factual claim in a CAM must deep-link to a document page **and bounding box**, and every extraction carries a computed confidence that propagates into the agent-confidence algebra (ARCHITECTURE.md §5, §8). That makes text geometry — bboxes and per-line confidences — a *load-bearing output* of the OCR stage, not a nice-to-have. An OCR engine that returns beautiful text with no coordinates cannot power citation pins, and an engine with no confidence signal gives the escalation logic and the confidence pipeline nothing to consume.

The operating constraints are a solo developer on Windows 11 running everything through `docker compose up`: the OCR stage runs inside the Celery worker image (CPU only, no GPU assumption), must not bloat or destabilise the image build, and must keep working when the Gemini API is rate-limited or unavailable — ingestion dying on API quota would violate the "demoable without API luck" requirement. Finally, LLM-based OCR has a failure mode uniquely dangerous in credit: it hallucinates *plausible* digits. A misread turnover figure that still sums correctly is worse than a visibly garbled one. Whatever chain we choose must be gated by deterministic validation, never by trust.

## Decision

A **text-layer-first, three-tier escalation chain**, executed per page inside the `ocr_pages` ingestion stage (§7 of ARCHITECTURE.md), with every line tagged by originating engine:

- **Tier 0 — text layer (no OCR).** `pypdfium2` extracts each page's text layer; the page is scored on character count, alphanumeric ratio, and dictionary-hit rate. Pages above threshold skip OCR entirely; only pages marked `needs_ocr` proceed. This is per-page precisely because Indian credit files are hybrid PDFs.
- **Tier 1 — RapidOCR (primary).** PP-OCRv4 detection + recognition models running on **ONNX Runtime** — PaddleOCR's accuracy without the PaddlePaddle framework. Pages render at 300 DPI with orientation correction; output is `OcrLine[]` with normalized bboxes and per-line confidence. Free, local, CPU-light in Docker on a Windows host, and the only tier that yields the line geometry the citation system needs.
- **Tier 2 — Gemini 2.5 Flash vision.** A page escalates when any of: mean line confidence < 0.80; implausibly low text density for a non-blank page; or the page hosts a table the doc-type spec requires. Flash reads stamps, faint dot-matrix, and handwriting that defeat classical OCR.
- **Tier 3 — Gemini 2.5 Pro vision.** Used only when Flash output fails the deterministic arithmetic validators (running-balance continuity, GSTR-3B tax arithmetic, row sums). One escalation, not a loop.
- **Validator gate (anti-hallucination).** Vision-extracted numerics must pass the stage-7 deterministic post-validators or the affected fields are flagged `LOW_CONFIDENCE` for the review UI. Vision output is never silently accepted — this rule is structural, not a prompt instruction.
- **Citation granularity policy** (honest provenance at the best available precision): word-level bboxes via `pdfplumber` for born-digital pages; line-level bboxes via RapidOCR for scanned pages; page-level + exact quote for vision-only pages. The `Citation.bboxes` list may be empty, signalling a page-level pin; the viewer degrades gracefully.
- **Tables:** born-digital → `pdfplumber` with per-doc-type settings; scanned tables and pdfplumber structural-sanity failures → Gemini vision emitting a JSON grid, gated by the same numeric validation. Spreadsheets (XLSX/CSV) never touch OCR — openpyxl/pandas path.
- **Operational shape:** OCR runs on a dedicated lower-concurrency Celery queue (CPU-heavy); per-page fan-out via chord; every `OcrLine` records `engine ∈ {text_layer, rapidocr, gemini_flash, gemini_pro}` so provenance survives into the evidence ledger.
- **Scope of the chain:** DOCX embedded full-page images route through the same chain; password-protected PDFs (common for Indian bank statements) pause upstream as `NEEDS_PASSWORD` and re-enter at Tier 0 once unlocked — the chain itself never handles credentials.

Cost sanity: ~100 fully scanned pages ≈ 0.1M image tokens ≈ **$0.03 on Flash**. The local primary is therefore justified by **bbox provenance and rate-limit resilience, not cost** — we state this honestly rather than pretending Gemini OCR is expensive.

## Alternatives considered

### Tesseract as primary
The default answer, and free — but the weakest of the candidates on precisely the failure modes this corpus maximises: skewed photocopies, rubber stamps overlapping text, dot-matrix bank printouts, and mixed Hindi/English. Choosing it as primary would push a much larger share of pages to the paid vision tier (negating its one advantage) while producing lower-quality line confidences for the escalation trigger. Its TSV-based table reconstruction is also unreliable on real Indian scans, so it buys nothing on the table path either.

### Full PaddleOCR
Same PP-OCRv4 models as RapidOCR — so identical accuracy — but it drags the entire PaddlePaddle deep-learning framework into the worker image: a heavy dependency with a history of brittle wheels, particularly painful in Docker on a Windows host. RapidOCR runs the exact same models on ONNX Runtime, a small, stable, CPU-friendly dependency the image already tolerates. Paying PaddlePaddle's build and debugging tax for zero accuracy gain fails the "every component must earn its box" test.

### Gemini vision only (no local OCR)
Superficially the simplest design and the best raw accuracy on genuinely messy scans — but it has a fatal flaw for this platform: Gemini returns **no reliable word or line geometry**, so citation deep-links (`?doc=…&page=…&highlight=…`) become unimplementable for every scanned page, gutting the explainability invariant the product is built on. (This was flagged as the disqualifying defect in one panel proposal.) It also makes ingestion throughput hostage to API rate limits and puts an engine that hallucinates plausible digits in the *primary* position rather than behind a validator gate. Fuzzy-matching Gemini text back onto locally detected boxes was considered and rejected: silently wrong highlights are worse than honest page-level ones.

### Google Document AI / Azure Document Intelligence
Purpose-built document OCR with genuine bbox output and strong table models — but per-page metered pricing, a second cloud credential, and a hard external dependency in the ingestion hot path of a local-first, demoable-offline system. The replay-gateway and quota-resilience story (ARCHITECTURE.md §5) assumes exactly one external LLM dependency; adding a second cloud OCR service reintroduces the failure mode the local primary exists to eliminate, for accuracy we can already reach via the Flash escalation tier at ~$0.0003/page.

### Uniform page-level citations (sidestep the geometry problem)
If every citation were page-level + quote, any OCR engine would do and Gemini-vision-only becomes viable. Rejected because it throws away the precise coordinates available for the large majority of pages: born-digital pages have word-level geometry for free via `pdfplumber`, and RapidOCR supplies line-level geometry for scans. Degrading everything to the worst case to simplify the OCR choice inverts the priority — the citation experience is a headline product feature ("hover a claim, see the highlighted line"), and the tiered-granularity policy preserves it wherever the data honestly supports it.

### Camelot for tables
Rejected alongside the OCR decision because it shares the pipeline: Camelot requires a Ghostscript system dependency in the image and is strongest only on lattice (ruled) tables — a capability `pdfplumber` already covers without system packages. The scanned-table path goes straight to Gemini vision regardless, so Camelot would occupy a niche that does not exist in this chain.

## Consequences

**Positive.** Scanned pages get honest line-level citation pins and per-line confidences that feed the escalation trigger and the confidence algebra with measured, not self-graded, signals. Ingestion survives Gemini outages and rate limits — the primary tier is fully local, so a demo never stalls on quota. Marginal OCR cost is effectively zero for typical caseloads. The validator gate means a hallucinated-but-plausible digit is caught by arithmetic (running balances, GST tax math, A = L + E) rather than by hope, and `LOW_CONFIDENCE` flags route it to human review. Engine tagging per line keeps provenance auditable months later through the evidence ledger.

**Negative / accepted.** Scanned pages get line-level (not word-level) highlights, and vision-only pages degrade to page-level + quote — a visible, documented precision loss we accept over faking precision. The three-tier chain is more code than any single engine: two escalation triggers, a validator gate, and four engine tags to test. PP-OCRv4's shipped recognition models cover Latin script; vernacular documents (Hindi board minutes, regional-language legal notices) are out of scope for v1. The 0.80 escalation threshold and the density heuristic are informed initial values, not measured optima. ONNX models add tens of MB to the worker image — accepted as trivial against the PaddlePaddle alternative.

**Follow-up obligations.**
- Build the golden messy-scan corpus (render → print/scan-degrade fabrication pipeline: skew, noise, stamps) and wire extraction field-F1 into the eval harness; tune the 0.80 threshold and density heuristic against it (Phase 6/8, per ARCHITECTURE.md §14).
- Tune OCR-queue concurrency and vision chord fan-out width to the actual Gemini quota tier once known (Phase 4, ingestion build-out).
- If vernacular documents enter scope, add RapidOCR's Devanagari recognition model and revisit the FTS analyzer configuration (backlog, gated on requirement).
- Record vision-tier request/response pairs through the replay gateway so ingestion tests run without API keys, matching the agent-runtime CI seam (Phase 5 — depends on the replay gateway, a Phase-5 deliverable).
- Surface per-engine page counts and vision-tier spend (already captured in `llm_calls`) on the ops dashboard; a rising escalation rate is the early-warning signal that scan quality shifted or the Tier-1 threshold needs retuning (Phase 8, ops profile).

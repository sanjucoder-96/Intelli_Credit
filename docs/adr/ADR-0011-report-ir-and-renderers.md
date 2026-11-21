# ADR-0011: Reports: structured IR -> Jinja2 -> HTML/WeasyPrint PDF/docxtpl DOCX/python-pptx

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

The report is the product's deliverable: a Credit Appraisal Memo (or the workflow-specific equivalent) that a bank credit committee, an auditor, or a hackathon judge will probe claim by claim. Two product invariants bear directly on how it is produced. First, explainability is structural, not prompted — every sentence in the CAM must trace to an evidence row (document page + bounding box, or an archived web snapshot). Second, numbers come from deterministic code: the score, limit (MPBF Tandon Method II, RBI turnover method), and rate are pure functions printed by the risk engine, and the LLM narrates within those bounds. A report pipeline in which the LLM emits finished prose directly into a document destroys both invariants at the last mile — prose cannot be mechanically checked for citation coverage, and a fluent paragraph can quietly invent a DSCR that appears nowhere in the `ExplanationBundle`.

The platform also promises that workflow #13 ships as configuration. Twelve workflows each need their own report look (CAM for credit appraisal, vendor scorecard for vendor risk, ESG memo, committee slide deck), in the formats banks actually use: PDF for the record, DOCX for the credit officer to edit, PPTX for the committee, XLSX for data annexures. If report layout is imperative code, every workflow forks the renderer and the extensibility promise dies in the last subsystem built.

Finally, the operating constraints: one developer, Windows 11, `docker compose up`, and a live demo that must not depend on a flaky rendering toolchain. The renderer runs inside the existing Celery worker image; anything that adds hundreds of megabytes, a browser sandbox, or a TeX distribution to that image is a standing tax on every build and every debug session.

## Decision

Content and presentation are separated at a schema boundary, and every renderer consumes the same validated intermediate representation.

- **Report IR.** The CAMGenerator/ReportWriter agents emit a Pydantic-validated `ReportIR`: an ordered tree of sections containing typed blocks — narrative claims, key-value panels, tables, chart specs. Each factual claim is a typed object whose `evidence_ids: list[EvidenceId]` field is schema-required (`min_length=1`) and whose confidence band rides along. The IR is generated per-section through the single `StructuredCaller` (Gemini `response_schema` + Pydantic re-validation + repair loop, max 2) and assembled, respecting Gemini's response-schema nesting limits (ARCHITECTURE §5). The assembled IR is a persisted, versioned artifact — diffable across run revisions.
- **Schema-enforced citation refusal.** The renderer resolves every `evidence_id` against the evidence ledger before rendering. An uncited factual claim cannot exist (the schema forbids it) and a dangling citation is a typed render failure routed through the retry ladder — never a silently rendered sentence. Refusal is structural, not prompt-hoped.
- **ExplanationBundle post-validation.** A deterministic post-validator extracts every numeric token from IR narrative fields and matches it (after unit/rounding normalization — ₹ crore vs absolute, percentage precision) against the numbers present in the `ExplanationBundle` (ScoreBreakdown terms, SHAP reason codes, limit/rate arithmetic, cited extractions). Narrative citing a number absent from the bundle is rejected and fed back through the repair loop; on exhaustion the report node parks `degraded` with a HITL flag.
- **Jinja2 templates from the TemplateRegistry.** Templates live in git at `workflows/report_templates/*.j2`, are registered per workflow in the `TemplateRegistry`, and are referenced by name from the immutable workflow definition (`template: cam_v2.html.j2`) — so a 2026 run re-renders identically forever. Indian number formatting (lakh/crore digit grouping, ₹ symbol, IC-grade labels) is done by deterministic Jinja2 filters, never by the LLM.
- **HTML is the single presentation source.** The in-app report viewer renders the template's HTML (with hover-to-evidence deep links `?doc&page&highlight`); **WeasyPrint** converts the *same* HTML plus a print stylesheet (`@page`, running headers, `break-inside: avoid` on tables) to PDF. Screen and PDF never diverge because they are one artifact.
- **Other formats.** **docxtpl** renders DOCX from a real `.docx` template carrying Jinja2 tags — one templating language, two carriers, and the bank's Word CAM format is preserved for officer editing. **python-pptx** builds the Presentation agent's deck from a slide-outline IR via one generic block→slide builder. **openpyxl** writes Excel annexures (ratio tables, ScoreBreakdown, GSTR-2A↔3B reconciliation grids) directly from typed rows with no LLM involvement.
- **Block vocabulary alignment.** IR chart-spec and table blocks reuse the same fixed block vocabulary the server-driven dashboards consume (`risk_gauge`, `table`, `timeline`, `heatmap`, `network`, `narrative`, `evidence_chain` — ARCHITECTURE §4), so a workflow's report and its dashboard are fed by one data shape.
- **Execution.** Rendering runs in the Celery worker as the graph's report stage; artifacts are stored content-hashed via the `StoragePort`. Fonts (Noto Sans, with the ₹ glyph) are baked into the worker image so PDF output is deterministic across machines.

## Alternatives considered

### LLM generates the whole document
Let Gemini 2.5 Pro write finished Markdown/HTML and convert it. Rejected because it is unauditable by construction: citation coverage cannot be mechanically verified inside free prose, numbers drift from the deterministic score ("citation drift" — the failure mode where the sentence survives but its evidence link no longer supports it), formatting is unstable across runs so golden-file tests and run-revision diffs are impossible, and the renderer-refusal mechanism has nothing typed to refuse. This single choice would convert invariants 1 and 2 from guarantees into hopes.

### Headless-Chromium HTML-to-PDF (Playwright/Puppeteer; wkhtmltopdf)
Chromium gives the fullest CSS support, but costs hundreds of megabytes in the worker image and is notoriously flaky in containers (sandbox flags, `/dev/shm` sizing, zygote crashes) — precisely the class of nondeterminism a demo-critical, laptop-hosted system cannot carry, and a browser process per render competes with OCR and agent work for the same CPU. wkhtmltopdf is effectively unmaintained on a frozen WebKit. WeasyPrint is a pure-Python renderer over a small set of system libraries (Pango/HarfBuzz/FontConfig) installed via apt in the worker image — no browser, no JS engine, no TeX — deterministic, and renders the identical HTML the viewer already uses. The accepted price is WeasyPrint's CSS subset (no JavaScript), addressed under Consequences.

### LaTeX
Typographically superior for long documents, but the toolchain is a multi-gigabyte TeX Live layer in the image; templating is hostile (escaping LLM-derived text into TeX is a security-and-correctness swamp); Indian-format financial tables (lakh/crore grouping, multi-band GST reconciliation grids) are genuinely painful in `tabular`; and there is no HTML parity, so the screen viewer and the PDF become two diverging implementations — the exact defect this decision exists to prevent.

### reportlab / hand-built python-docx layout
Imperative canvas or paragraph-object layout code per report means every workflow's report is a code fork — a direct violation of "workflow #13 is a configuration exercise" — and solo-dev weeks spent on box arithmetic instead of the platform. docxtpl keeps DOCX template-driven: the layout lives in a `.docx` a non-programmer could edit, and the code only supplies the validated IR context. python-docx remains underneath docxtpl but is never used for hand-built layout.

## Consequences

**Positive.**
- Every rendered sentence carries resolvable citations; "walk the judge through the logic" survives adversarial probing because the renderer physically cannot emit an unbacked claim, and the numeric post-validator closes the last gap between LLM narrative and the deterministic `ExplanationBundle`.
- Screen and PDF are one HTML artifact — no divergence bugs, and the Phase 7 report viewer gets hover-to-evidence for free from the same markup.
- A new workflow's report is one `.j2` template (plus optionally a `.docx` carrier) registered in the `TemplateRegistry` — registration, not code.
- The IR is a stable JSON artifact: golden-file CAM tests (Phase 8) diff IR and rendered HTML; the replay gateway reproduces both byte-identically without API keys.
- The render path is a pure-Python renderer over a small set of system libraries (Pango/HarfBuzz/FontConfig) installed via apt in the worker image: no GPU, no browser sandbox, no TeX.
- Because the IR carries evidence IDs per claim, the correction cascade (ARCHITECTURE §15.4) can mark exactly the dependent report sections dirty when a human correction supersedes a finding, and a scoped re-render replaces only what changed — recorded as a new run revision.

**Negative / accepted trade-offs.**
- WeasyPrint executes no JavaScript, so the interactive ECharts visuals in the app do not appear in the PDF. Chart blocks destined for print are pre-rendered as static SVG (WeasyPrint renders SVG well) via Node.js bundled in the worker image solely for ECharts server-side SVG rendering — the same chart specs the web UI consumes, so there is one charting system and screen/PDF parity. The image-size cost of carrying Node is accepted, and some interactivity (network-graph exploration) simply stays app-only.
- Pagination control is weaker than LaTeX for very long tables; mitigated with print CSS (repeated `thead`, `break-inside: avoid`), and accepted as adequate for CAM-length documents.
- DOCX is a separate carrier from HTML: minor visual divergence between DOCX and PDF is accepted — PDF is the record, DOCX is the editable working copy.
- The numeric post-validator needs careful unit normalization (₹ Cr vs lakh vs absolute, rounding tolerance) or it will false-reject honest narrative; this normalizer is deterministic code that must be tested as seriously as the scorecard.
- `ReportIR` becomes a versioned contract in the `SchemaRegistry`: IR schema changes require a version bump, and templates must declare the IR version they consume.

**Follow-up obligations.**
- Phase 5: `ReportIR` schema + per-section generation, renderer refusal, ExplanationBundle post-validation; acceptance already requires every CAM claim to resolve to an evidence row.
- Phase 7: report viewer over the HTML IR with citation hover; static-SVG rendering for print chart blocks (Node.js + ECharts SSR in the worker image).
- Pin the Pango/HarfBuzz/FontConfig packages in the worker Dockerfile; native-Windows debugging of the PDF path happens inside the container.
- Phase 8: golden-file CAM tests (IR JSON + HTML), citation-precision eval in the harness.
- Revisit if a tenant demands pixel-perfect letterhead beyond print-CSS capability: the fallback is a DOCX-first flow for that tenant, not a Chromium adoption.

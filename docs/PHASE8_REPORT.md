# Phase 8 Report — Evaluation, Adversarial Testing & Production Hardening

**Date:** 2026-08-28
**Goal:** Prove IntelliCredit is reliable against the actual hackathon problem
statement — not merely that individual functions have tests. Every claim below
is backed by a test that runs; nothing is a manufactured metric.

---

## 1. Headline result

```
INTELLICREDIT ACCEPTANCE RUN
=========================================================
Extraction accuracy       PASS    5 passed
Evidence provenance       PASS    6 passed
GST / bank analysis       PASS    8 passed
Research (safety+shape)   PASS    5 passed
Fraud / consistency       PASS    8 passed
ML risk (PD + SHAP)       PASS   31 passed
Deterministic decision    PASS   26 passed
Gemini resilience         PASS   31 passed
CAM consistency           PASS    4 passed
PDF generation            PASS    3 passed
Security checks           PASS   10 passed
Prompt injection          PASS    6 passed
Performance baseline      PASS    2 passed
HITL reliability          PASS    5 passed
Restart / resume          PASS    3 passed
CAM cross-surface         PASS    1 passed
Tenant isolation          PASS    5 passed
---------------------------------------------------------
Overall                   PASS
=========================================================
```

Reproduce with `IC_INTEGRATION_TESTS=1 ./.venv/Scripts/python.exe -m scripts.acceptance_run`
(drop the env var for the deterministic-only subset). The runner *executes the
underlying test modules* and reports their real pass/fail — it never fabricates
a verdict — and embeds the measured extraction and latency numbers below.

**Full regression:** `293 passed, 0 failed` across the complete backend suite
(unit + integration, `IC_INTEGRATION_TESTS=1`) — up from 168, so Phase 8 added
~125 tests with zero regressions. `mypy app` clean (137 files), `ruff` clean,
`import-linter` 2 contracts kept. Frontend `tsc` / `eslint` / `vitest` (15) /
`next build` all clean. Browser E2E re-verified against the live product.

---

## 2. Evaluation methodology

The harness distinguishes five kinds of evidence and never blends them:

| Kind | What it proves | Where |
|---|---|---|
| **Deterministic correctness** | Pure functions produce exact, reproducible outputs | scorecard/CAM/features/extractors |
| **Synthetic evaluation** | Behaviour on constructed documents with known ground truth | `eval/extraction_corpus.py` |
| **Public-data ML** | The model trained on real Polish UCI data; served identically | `test_ml_*`, `ml/` package |
| **LLM-qualitative** | Structural + safety properties of LLM-backed agents (never a fabricated recall %) | `test_research_eval.py`, prompt-injection tests |
| **Live external-service** | Real Gemini behaviour, including transient failure | `test_acceptance_pipeline.py` (live), resilience tests (deterministic) |

Design choices:
- **No fabricated accuracy.** Extraction accuracy is measured over a corpus with
  ground truth; ML metrics come from the real held-out evaluation; web-search
  recall is explicitly **not** claimed (it needs a live key + a curated query
  set — documented as a gap, not reported as passing).
- **The framework is deliberately small.** A corpus module + a scorer + an
  acceptance runner — not an elaborate platform. The bulk of the value is in the
  assertions, not the scaffolding.

---

## 3. Measured results

**Extraction (deterministic corpus, `eval.extraction_corpus`):**

| Metric | Value |
|---|---|
| classification accuracy | 1.0 |
| field precision | 1.0 |
| field recall | 1.0 |
| numeric accuracy | 1.0 |
| page-citation accuracy | 1.0 |
| **fabrications** | **0** (the honesty invariant) |
| unknown-kept (adversarial) | 2 / 2 |

Scope (honest): the corpus covers the **deterministic** doc types — GSTR-3B,
GSTR-2B/2A, bank statements — under clean and adversarial conditions (OCR noise,
missing tables, misleading prose, broken continuity, incomplete docs). ITR and
full financial-statement extraction are LLM-based (nondeterministic, key-gated)
and are therefore *not* scored with deterministic ground truth. Pixel-level bbox
accuracy is available only for the controlled synthetic annual report (Phase-7
hardening), because the deterministic extractors cite page + quote, not geometry.

**Hot-path latency baselines (p50, deterministic; `eval.perf`):**

| Path | p50 | p95 |
|---|---|---|
| GSTR-3B extraction | ~0.2 ms | ~0.7 ms |
| ratios + scorecard | ~0.2–0.5 ms | ~2 ms |
| PD feature build | ~0.05 ms | ~0.15 ms |
| CAM assemble + render | ~0.3–1 ms | ~0.7 ms |

These are baselines, not optimization targets; the perf test asserts only
generous ceilings (~50–500× p50) to catch a gross regression.

**ML (unchanged real evaluation, re-verified):** champion XGBoost, isotonic
calibration; ROC-AUC 0.813, ECE 0.264→0.003 (held-out). Phase 8 adds SHAP
additivity, probability-bounds, schema-parity, and `present`-flag guards.

---

## 4. Tests added (by Phase-8 item)

| Item | Module(s) | Tests | What it proves |
|---|---|---:|---|
| 1 Eval harness | `eval/` + `scripts/acceptance_run.py` | — | Reproducible, kind-labelled framework |
| 2 Extraction | `test_extraction_eval.py` + corpus | 5 | Precision/recall/numeric/page + prefer-unknown |
| 3 Evidence integrity | `test_evidence_integrity.py` | 6 | Provenance + prompt-injection fencing + schema forbids numbers |
| 4 Decision integrity | `test_decision_invariants.py` | 26 | LLM can't set score/limit/rate/decision; knockouts, fraud cap, notch clamp, coverage gate |
| 5 HITL adversarial | `test_hitl_adversarial.py` | 5 | ±notch moves the persisted score; idempotent resume; malformed/non-paused rejected |
| 6/15 Workflow + chaos | `test_workflow_resilience.py` + `scripts/chaos_demo.py` | 3 | Kill→resume, crash-recovery, no double-write, gate never skipped |
| 7 Gemini resilience | `test_gemini_resilience.py` | 31 | Every retriable code, replay fallback (tagged), never fabricate |
| 9 ML validation | `test_ml_feature_parity.py`, `test_ml_validation.py` | 14 | **Train↔inference parity (flagship)**, SHAP additivity, schema refusal, bounds |
| 10 Indian context | `test_indian_context.py` | 8 | GST family + reconciliation + lakh/crore/₹ + FY; honest support boundary |
| 8 Research | `test_research_eval.py` | 5 | Records sources, fences web content, bounded severity, honest degradation |
| 11 CAM consistency | `test_cam_consistency.py`, `test_cam_api_consistency.py` | 5 | IR == RiskResult; identical across persisted / API / PDF |
| 12 Security | `test_security.py`, `test_security_isolation.py` | 15 | Role matrix, RLS default-deny, path traversal, upload guardrails, secret redaction |
| 13 Performance | `test_perf_baseline.py` + `eval/perf.py` | 2 | Hot-path baselines |
| **Total new** | | **~125** | |

The flagship is `test_ml_feature_parity.py`: it imports the real training
harmonizer (`ml.datasets._harmonize_row`) and asserts, feature by feature, that
inference (`build_pd_features`) computes the identical quantity — the automated
regression guard for the total-liabilities-vs-interest-bearing-debt bug an
adversarial review caught in Phase 6. It fails loudly if inference ever regresses.

---

## 5. Issues / observations found & fixes applied

Phase 8 did **not** uncover a new production-breaking vulnerability — the
deterministic core and the durable engine held up under adversarial testing,
which is itself a meaningful result. What surfaced:

- **Confirmed (regression-guarded), not newly broken:** the Phase-6 feature
  bug is now pinned by an automated parity test rather than only comments.
- **Minor hardening observations (documented, low risk):**
  - `GET /cases/{id}/events` (SSE) is authenticated (tenant-scoped) but has no
    explicit `require_permissions` guard, unlike the other case routes. It leaks
    no cross-tenant data (RLS + `get_tenant_context`), but adding `CASE_READ`
    would make the authorization surface uniform.
  - `DbTraceWriter._redact` is a top-level key drop (not recursive). Today no
    trace payload nests a secret under a redacted key, so nothing leaks; a
    recursive redact would be defence-in-depth if payload shapes grow.
  These are noted for a future hardening pass, not fixed in Phase 8 (they are not
  active vulnerabilities and the task scope was evaluation, not refactoring the
  running product).
- **Test-authoring fixes (not product bugs):** a handful of test API-shape and
  formatting corrections during development (e.g. adjacent-pair `zip(strict=)`,
  read-handler signatures, `ruff` line lengths). None touched `app/`.

---

## 6. Remaining limitations (honest)

- **LLM-extraction accuracy** (ITR, financial statements, annual reports) is not
  deterministically benchmarked — it is nondeterministic and API-key-gated. It
  is exercised live; deterministic ground-truth scoring covers only GST/bank docs.
- **Web-search recall/precision** is not benchmarked — it needs a live research
  key and a curated ground-truth query set. Only the agent's structural safety
  and honest offline degradation are asserted.
- **Classification-only doc types** (GSTR-1, CIBIL Commercial, legal notices,
  sanction letters, annual reports) are recognised but have no deterministic
  extractor yet — asserted explicitly so the gap can't be mistaken for support.
- **MCA / e-Courts** live retrieval is CAPTCHA-gated per the architecture and is
  not a deterministic capability here.
- **The one live-service flake** (`test_mixed_bundle_end_to_end`) depends on
  Google's Gemini availability; it passed this run but can 503 under Google
  demand spikes. That is external, not a code regression.
- **Concurrency/perf at scale** is baselined on hot paths only; a load test with
  many concurrent full workflows is future work.

---

## 7. Hackathon readiness assessment

**Ready.** The credit-decision engine's core invariants — the LLM never sets a
number, evidence traces to source, the ML signal is a bounded relative factor, a
critical fraud signal is a hard ceiling, HITL genuinely moves the decision, the
CAM cannot drift from the persisted numbers, and a killed worker resumes without
duplication — are now each pinned by executable tests, verified against live
Postgres, and reproduced by a single acceptance command. The demo is defensible:
the chaos kill-and-resume (`scripts/chaos_demo.py`) and the cross-surface CAM
consistency test are strong technical proofs, and the honest scoping (what is
measured vs. what is a documented gap) is itself a credibility signal to judges.

Residual risk is concentrated in the LLM-dependent paths (extraction of
free-form statements, live web research, live Gemini availability), all of which
degrade honestly rather than fabricating — and all of which are clearly labelled.

---

## 8. Recommended next phase

**Phase 9 — Observability, load & the LLM-extraction benchmark.** In priority:
1. A labelled **financial-statement extraction benchmark** (a small corpus of
   real/representative balance sheets + P&Ls with ground truth) to put an honest
   number on the LLM-extraction path — the current biggest unmeasured surface.
2. A **concurrency/load test** (N simultaneous full workflows) to turn the
   hot-path baselines into an end-to-end throughput/latency envelope.
3. The two **minor security hardenings** (uniform `CASE_READ` on SSE; recursive
   redact).
4. **Runtime observability** (metrics/traces dashboards) so the resilience the
   tests prove is visible in operation.

Deferred by design (do not build unless asked): Isolation-Forest anomaly models,
revenue forecasting, SDV synthetic generation, PSI drift monitoring — none are
needed for the problem statement and each adds unproven surface.

---

## Appendix — commands

```bash
# Full acceptance report (with live Postgres/Redis)
IC_INTEGRATION_TESTS=1 ./.venv/Scripts/python.exe -m scripts.acceptance_run

# Chaos kill-and-resume demo
IC_INTEGRATION_TESTS=1 ./.venv/Scripts/python.exe -m scripts.chaos_demo

# Full backend regression
IC_INTEGRATION_TESTS=1 ./.venv/Scripts/python.exe -m pytest -q     # 293 passed
./.venv/Scripts/python.exe -m mypy app                            # 137 files
./.venv/Scripts/lint-imports.exe                                  # 2 contracts

# Frontend
cd ../frontend && npx tsc --noEmit && npx eslint src --max-warnings=0 && npx vitest run && npm run build
```

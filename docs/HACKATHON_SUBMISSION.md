# IntelliCredit — Hackathon Submission

A judge-readable summary. Numbers here are measured from the repo or explicitly
marked UNMEASURED; nothing is invented.

---

## Problem

Corporate credit teams drown in **fragmented, heterogeneous evidence** — GST
returns, ITRs, bank statements, annual reports, contracts, legal notices, plus
whatever the web knows about the borrower and its promoters. Assembling this into a
defensible decision is slow and manual. The obvious shortcut — hand it to a large
language model and ask for a rating — is unacceptable for a regulated lender,
because an LLM will **confidently hallucinate a number** (a score, a limit, a rate)
and can't prove where any of its claims came from.

## Solution

IntelliCredit turns a company's evidence pack into a **citation-backed,
quantitatively-auditable, human-reviewed credit decision**. An agentic pipeline
ingests and extracts documents, researches the web, computes financial and fraud
signals, runs a calibrated ML default-risk model, pauses for a credit officer, then
produces the decision and a Credit Appraisal Memo (CAM) — with every number
deterministic and every claim linked to its source.

## Innovation

**The LLM does not decide credit.** Responsibilities are split so that no single
component can fabricate a decision:

- LLM → extracts, researches, writes narrative (always cited; never a final number).
- Deterministic code + calibrated ML → compute the quantitative risk.
- Policy engine → determines decision / limit / rate as printed arithmetic.
- Human → reviews qualitative context and can override; the run pauses for them.

This is enforced in code (the scorecard consumes only typed numbers; the CAM
re-derives figures deterministically; the renderer refuses uncited claims), not
merely intended.

## Architecture

A modular monolith: one FastAPI backend (run as API + Celery worker + beat), a
Next.js frontend, and exactly two stateful services (PostgreSQL 17 + pgvector,
Redis 7) — one `docker compose up`. Postgres is the everything-store (records,
vectors, FTS, LangGraph checkpoints, evidence/trace ledger, model registry, flags).
Workflows are **data** (YAML → immutable versions → one `GraphCompiler` →
LangGraph). Full detail: [ARCHITECTURE.md](ARCHITECTURE.md);
decisions: [ARCHITECTURE_WALKTHROUGH.md](ARCHITECTURE_WALKTHROUGH.md).

```text
Documents → Evidence ledger → [Financial ∥ Research ∥ Fraud] → Human review
          → ML default risk → Deterministic scorecard/policy → Decision → CAM
```

## Explainability

Structural, not prompted. Every result walks backward to its source:
**Evidence (page + bounding box, or web snapshot) → extracted value → ratio →
score contribution → decision.** The evidence ledger is one write path serving the
citation store, the agent trace, and the judge-facing "explain this" UI. In the app,
clicking a cited value opens the document page with the region highlighted.

## AI / ML

- **PD model:** XGBoost champion, **isotonic-calibrated**, with **TreeSHAP** reason
  codes; a 14-feature schema shared identically by training and inference (a hard
  guard refuses to predict on mismatch). Measured on a held-out test split of the
  Polish UCI #365 dataset: **ROC-AUC 0.81, PR-AUC 0.31, KS 0.49, Brier 0.038, ECE
  0.003** (calibration reduced ECE from 0.26). Reason codes are exact log-odds
  TreeSHAP contributions, grouped and ranked.
- **Honest fit:** trained on *foreign* public bankruptcy data (no realized Indian
  default data is obtainable), so the PD is a **relative** signal re-normalized as
  one 20% factor in the scorecard — never an absolute Indian PD. Labeled in-product.
- **LLM usage:** Gemini Flash/Pro via one gateway, structured output with schema
  re-validation + a bounded repair/escalation ladder; prompts fence untrusted
  document/web content against injection.

## Human-in-the-loop

The run pauses at a real `interrupt()` gate (checkpointed in Postgres). The credit
officer adds observations mapped to bounded scorecard notches (clamped ±12) and can
toggle knockouts; on resume the score is **recomputed** and the before/after delta
is recorded in the trace. Only the `credit_officer` role can resolve a review.

## Security

- **Tenancy:** mandatory `TenantContext` repository scoping **plus** Postgres
  row-level security as an independent second layer — cross-tenant leakage requires
  two simultaneous bugs.
- **AuthN/Z:** first-party JWT (argon2 hashing), `org_id` + role claims, per-route
  permission guards (incl. the SSE event stream).
- **Prompt injection:** document/web content is treated as untrusted; prompts
  separate instructions from evidence; the scorecard is deterministic so injection
  can't move a number.
- **Secrets:** no secret has a source-code default; `.env` is gitignored; trace
  payloads are **recursively redacted** at write time; secrets never reach logs,
  traces, or the frontend bundle.
- Upload guardrails (extension allowlist, size cap, ZIP handling), PDF parsing in
  the worker not the API. Regression-tested in `tests/test_security*.py`.

## Scalability

- Deterministic hot paths are sub-millisecond p50 (extraction ~0.2 ms, scorecard
  ~0.2–0.5 ms, PD features ~0.05 ms, CAM render ~0.3–1 ms) — see
  [PHASE8_REPORT.md](PHASE8_REPORT.md).
- A concurrent-workflow load harness (`scripts/load_test.py`, offline-scripted LLM)
  runs 5/10/25 concurrent runs and reports p50/p95/max/throughput/failure-rate.
- Scale is deliberately laptop-sized (a case ≈ 4–8k chunks); the design scales up a
  single VM, not out to a cluster. Full concurrent load numbers: see
  [FINAL_STATUS.md](FINAL_STATUS.md).

## Extensibility

Workflows are configuration (ADR-0008): a YAML definition + document pack +
scorecard/policy config + report template, registered against the agent / predicate
/ document-type / model registries, with a never-forking server-driven frontend.
**One** workflow (`credit_appraisal`) is implemented; the mechanism is built and
unit-tested; the other eleven are a scheduled path, not shipped. See
[README §5](../README.md#5--one-engine-twelve-workflows).

## Demo

Five-minute script: [HACKATHON_DEMO.md](HACKATHON_DEMO.md). Setup + accounts:
[DEMO_SETUP.md](DEMO_SETUP.md). Offline-capable (no API keys); ends with a live
kill-and-resume proof of durable checkpointing.

---

## What's real vs. prototype

The credibility of this submission rests on being precise about this.

### Implemented and verified

| Capability | Evidence |
|---|---|
| Durable workflow orchestration (LangGraph + Postgres checkpoints) | `app/workflows/*`; chaos + resilience tests |
| Document ingestion + OCR escalation + validated extraction | `app/ingestion/*`, `app/docintel/*` |
| Evidence ledger (page + bbox + web snapshot provenance) | `app/infrastructure/db/models/evidence.py`; evidence-integrity tests |
| Deterministic scorecard + overlays + policy grid | `app/analysis/scorecard.py`; decision-invariant tests (26) |
| Calibrated, explainable ML PD (XGBoost + isotonic + TreeSHAP) | `pd_model@1.0.0` card.json; ML validation tests |
| Human-in-the-loop pause → officer note → resume → re-score | `credit_appraisal.yaml` HITL gate; HITL adversarial tests |
| CAM generation + PDF export | `app/analysis/cam.py`, `cam_pdf.py`; CAM consistency + PDF tests |
| Kill/restart → checkpoint resume, no duplication | `scripts/chaos_demo.py`; workflow-resilience tests |
| Full frontend over the real API | `frontend/src/**`; Phase-7 E2E verification |
| Metrics + tenant isolation (repo scoping + Postgres RLS) | `app/core/metrics.py`; security-isolation tests |

### Prototype / requires further production validation

| Area | Honest status |
|---|---|
| **Indian PD calibration** | Model trained on foreign (Polish UCI #365) data; a *relative* signal, not an Indian PD. No realized Indian default data exists to calibrate against. Labeled in-product. |
| **LLM extraction accuracy** (financial statements / ITR / annual reports) | **UNMEASURED** — nondeterministic, API-key-gated. A labeled corpus + runner exist to measure it when a key is supplied. (Deterministic GST/bank extraction *is* measured: 1.0, 0 fabrications, small wide-CI corpus.) |
| **Web-research recall / precision** | **UNMEASURED** — needs live keys + a ground-truth query set. Only the agent's safety + honest offline degradation are tested (6-query labeled corpus provided). |
| **Production-scale / concurrent load** | Hot paths baselined sub-ms; a 5/10/25 concurrent-workflow harness is provided. Not a claim of production throughput. |
| **External MCA / e-Courts integrations** | CAPTCHA-gated; treated as uploaded document types + cached corpora, not pretend-live integrations. |
| **Deferred-by-design ML** | Isolation-Forest anomaly model, revenue forecasting, SDV synthetic generation, and PSI drift monitoring are described in the architecture but **not built** (not needed for the problem statement). |
| **DOCX report export** | Deferred behind PDF (PDF is the demo path). |
| **Automated E2E (Playwright)** | Verification was manual in-browser; an automated E2E suite is future work. |

## Limitations

See the table above and [FINAL_STATUS.md](FINAL_STATUS.md) §Limitations. The
recurring honest caveats: the ML PD is a relative (not Indian-calibrated) signal;
LLM-extraction and web-recall accuracy are unmeasured; and one workflow is
implemented (the rest are an extensibility mechanism).

## Future work

1. Supply `IC_GEMINI__API_KEY` (+ `IC_TAVILY__API_KEY`) and run the provided
   benchmark runners to replace the two UNMEASURED tiers with real numbers.
2. Author `vendor_risk` + `esg_analysis` as pure YAML to exercise and then freeze
   the workflow DSL (harvest-the-abstraction), and lift scorecard/policy/doc-pack
   into the declarative definition.
3. When Indian default data becomes available, recalibrate the PD model and drop
   the relative-signal caveat.
4. Automated Playwright E2E over the demo path; optional Grafana over `/metrics`.

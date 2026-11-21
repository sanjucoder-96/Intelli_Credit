# IntelliCredit — Architecture Walkthrough

The ten decisions that most shape IntelliCredit, why each was made, and where the
full argument lives. This is a reader's guide to the [16 ADRs](adr/) — it does not
restate them. Each ADR carries Context → Decision → **Alternatives considered** →
Consequences, and argues rejected options from failure modes, not fashion.

Three invariants recur underneath every decision:

1. **Explainability is structural** — "walk the judge through the logic" is a
   database query, not a prompt.
2. **Numbers come from deterministic code and calibrated models; words come from
   the LLM** — a model can never invent a score, a limit, or a rate.
3. **Solo developer, Windows laptop, `docker compose up`, exactly two stateful
   services** (Postgres 17 + Redis 7) — every component must earn its box.

---

## 1 · Why LangGraph — and only for orchestration

**Decision:** LangGraph is the sole orchestration framework, used strictly
*between* agents; every model call goes through one owned `StructuredCaller` on the
google-genai SDK. No LangChain chains, no second agent runtime (Pydantic-AI).
→ [ADR-0003](adr/ADR-0003-langgraph-only-no-pydantic-ai.md)

**Why:** human-in-the-loop pause/resume, crash resumability, and parallel fan-out
are non-negotiable for a credit workflow, and LangGraph provides exactly those.
Adding a second framework would duplicate ~200 owned lines while still depending on
LangGraph "for everything hard." Inside a node, each agent runs a *bounded* async
micro-loop (plan → act → reflect → revise-once → score) — easier to test and trace
than a nested graph. The `GraphCompiler` (`backend/app/workflows/compiler.py`) is
the only code that touches LangGraph's builder API.

## 2 · Why durable checkpoints

**Decision:** every run is checkpointed to Postgres via
`langgraph-checkpoint-postgres` (`thread_id = run_id`); HITL is a real
`interrupt()`; resume is `ainvoke(None, thread_id)`. → [ADR-0003] + the
Celery×LangGraph idempotency rule in [ADR-0004](adr/ADR-0004-celery-task-orchestration.md)

**Why:** a credit run pauses for a human and may take minutes; it must survive a
worker crash or a redeployment without losing work or double-deciding. Because the
checkpoint is authoritative, at-least-once Celery delivery is made *provably
harmless*: a redelivered start that's paused reports without advancing; a mid-run
crash resumes without re-counting append-only channels; ingestion stages no-op on
their `document_stages` checkpoint. This is the property the chaos demo proves live
(`scripts/chaos_demo.py`): kill the worker, a fresh one resumes, exactly one
execution row.

## 3 · Why an evidence ledger

**Decision:** one append-only ledger (`evidence`, plus the `agent_executions` /
`llm_interactions` trace tables) is simultaneously the RAG citation store, the
agent observability trace, and the judge-facing explainability UI — one write path.
→ [ADR-0012](adr/ADR-0012-first-party-tracing.md), with
[ADR-0002](adr/ADR-0002-postgres-as-the-everything-store.md) and
[ADR-0011](adr/ADR-0011-report-ir-and-renderers.md)

**Why:** if the trace and the explanation are two systems, they drift, and a
third-party LLM-observability UI can't render citation pins, confidence components,
or scorecard arithmetic — nor should sensitive DPDP data be shipped to a cloud SaaS.
Because every claim row carries `document · page · bbox · quote · source` (or a
content-hashed web snapshot), dangling citations become *structurally impossible*
and the report renderer can schema-refuse any uncited factual claim.

## 4 · Why one LLM gateway

**Decision:** all model traffic flows through a single `LLMGateway` with static
per-node Flash/Pro tiering, one-shot Flash→Pro escalation, a Redis response cache,
a global token-bucket limiter, an in-state **USD cost circuit breaker**, and
record/replay. → [ADR-0005](adr/ADR-0005-llm-gateway-and-model-tiering.md)

**Why:** the call profile is bimodal (cheap classification vs. expensive
synthesis), so tiering saves real money; a hard per-run budget means a runaway loop
can never become an unbounded bill; and the cache + limiter keep a live demo from
dying on quota. Agents never see the SDK — one seam owns cost, retries, and
reproducibility.

## 5 · Why record/replay mode

**Decision:** the gateway records request/response pairs and can serve a recorded
run **byte-identically through the real SSE streaming path**. → [ADR-0005]

**Why:** it is simultaneously the CI seam (tests run with no API key) and the
demo-day offline fallback (no dependence on API luck). Crucially, on a transient
live failure the gateway can fall back to a recording, tagged
`served_from_replay_fallback`, and the run-mode badge shows DEGRADED — it **never
fabricates** an answer. Deterministic local embeddings ([ADR-0007]) complete the
offline story.

## 6 · Why a deterministic scorecard (not an LLM average)

**Decision:** score/grade/limit/rate come from a config-driven weighted-factor
scorecard with ordered, logged overlays feeding a deterministic policy grid; the
LLM only classifies unstructured inputs into enumerated factor levels.
→ [ADR-0009](adr/ADR-0009-deterministic-scorecard-and-policy-grid.md)

**Why:** invariant #2. `S = Σ_pillar W_p · Σ_factor w_f · n_f(x_f)`, every term
persisted with evidence refs, so the CAM prints the exact arithmetic and **prompt
injection cannot move a number**. Overlays (knockouts, fraud caps, bounded
qualitative notches, a coverage penalty) are ordered and logged; the limit is the
binding minimum of EBITDA-multiple / MPBF Tandon-II / turnover method; the rate is
base + grade premium + itemized adjustments. Implementation:
`backend/app/analysis/scorecard.py`.

## 7 · Why calibrated ML

**Decision:** the PD model is XGBoost with **isotonic calibration** and TreeSHAP
reason codes, trained only on real public bankruptcy data, entering the scorecard
as *one* factor (20%). → [ADR-0010](adr/ADR-0010-ml-datasets-and-synthetic-separation.md)

**Why:** an uncalibrated classifier's "probability" is not a probability —
calibration took ECE from 0.26 to ~0.003 on held-out data, which is what makes the
PD usable as a factor. TreeSHAP gives exact per-prediction contributions (why
XGBoost is the champion even though LightGBM scores marginally higher). And because
no realized Indian default data is obtainable, the output is honestly presented as
a **relative** signal, never an absolute Indian PD — with a UI badge and a note that
travels with every prediction. Synthetic data is demo-only, separated structurally
(a `data_origin` column, a CI gate that fails if training references synthetic).

## 8 · Why human-in-the-loop

**Decision:** the workflow pauses at an `interrupt()` gate; the credit officer's
note becomes a first-class finding that flows through a **re-scoring** node with a
logged before/after delta; only the `credit_officer` role can resolve a review.
→ [ADR-0003] + [ADR-0009] + [ADR-0013](adr/ADR-0013-auth-and-tenancy.md)

**Why:** a regulated credit decision cannot be fully automated, and "the AI must
adjust the final risk score based on the officer's input" must be *auditable*. The
note maps to a predefined factor level with a **bounded** notch (clamped ±12) so no
single note can swing a decision invisibly. When the ML grade and the qualitative
read diverge beyond a threshold, the run routes to HITL rather than papering over
the disagreement.

## 9 · Why Postgres + pgvector as the everything-store

**Decision:** one Postgres 17 (+pgvector) is the system of record, vector index
(HNSW), full-text search, LangGraph checkpoint store, evidence/trace ledger, model
registry, and feature flags. → [ADR-0002], with [ADR-0007](adr/ADR-0007-embeddings-local-first.md)

**Why:** at this scale (a case is ~4–8k chunks; even 100 demo cases stay under a
million) a dedicated vector DB would add a second consistency domain for no gain,
and the decisive force is *explainability*, not scale — index writes are
transactional with extraction, so a chunk, its document, its evidence, and its
tenant scope are always consistent. Hybrid retrieval (FTS + vector, fused with
Reciprocal Rank Fusion) runs in one SQL CTE. One backup, one migration story
(Alembic from the first table). Blobs are deliberately kept *out* of Postgres,
behind a `StoragePort` ([ADR-0016](adr/ADR-0016-blob-storage-port.md)).

## 10 · Why configuration-driven workflows

**Decision:** a workflow is authored as YAML, validated fail-fast into an
immutable numbered version, compiled by the single `GraphCompiler` over a set of
registries; the frontend renders server-driven dashboards from a fixed block
vocabulary so it never forks per workflow. → [ADR-0008](adr/ADR-0008-workflow-as-data.md)

**Why:** the platform's core asset is the mechanism that makes "workflow #13 a
configuration exercise, not a code fork," and every run pins the exact definition
version it executed so a 2026 decision stays explainable forever. **Honest scope:**
the mechanism is built and unit-tested, and `credit_appraisal` (v3) is the one
implemented workflow; the scorecard/policy/document-pack/report surfaces are still
typed Python (not yet lifted into the YAML), and the other eleven workflows are a
scheduled path, not shipped code. Per the project's "harvest the abstraction, don't
speculate it" rule, the DSL is frozen only after three real workflows — so this is
described as an extensibility *mechanism*, not a finished 12-workflow product. See
[README §5](../README.md#5--one-engine-twelve-workflows).

---

## Where to read more

- Full architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- All decisions with rejected alternatives: [adr/](adr/) (README table at
  [adr/README.md](adr/README.md))
- What's shipped vs. planned: [FINAL_STATUS.md](FINAL_STATUS.md) ·
  [HACKATHON_SUBMISSION.md](HACKATHON_SUBMISSION.md)

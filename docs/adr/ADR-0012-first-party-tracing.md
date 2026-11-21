# ADR-0012: Observability: the evidence ledger IS the trace (no LangSmith/Langfuse/Phoenix)

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit's first product invariant is that explainability is structural: every claim traces to evidence, every score is a printed formula, every agent step is replayable, and "walk the judge through the logic" must be a database query. Separately, the operator (the same solo developer) needs conventional LLM observability: which prompt ran, on which model, with what tokens, latency, cost, and failure mode. These are usually treated as two systems — a product-facing audit ledger and a developer-facing tracing tool (LangSmith, Langfuse, Phoenix). Two systems means two write paths recording the same events, which inevitably drift: the demo UI shows one story while the debugging tool shows another, and the audit artifact the platform *sells* is the one more likely to rot.

The operating constraints sharpen this. The platform runs local-first via docker-compose on a Windows 11 laptop with exactly two stateful services (PostgreSQL 17, Redis 7 — [ADR-0002](ADR-0002-postgres-as-the-everything-store.md)); every additional container must earn its box. The data being traced is adversarial and sensitive: uploaded GST returns, ITRs, CIBIL Commercial reports, and bank statements carrying PAN and account numbers — material a bank or NBFC buyer cannot allow to leave the machine (India's DPDP posture, §15.6 of ARCHITECTURE.md). And the demo must survive with zero network: a trace viewer that lives in someone else's cloud is a single point of failure on exactly the day it matters.

Finally, no third-party trace UI can render what our trace actually contains: per-step confidence *components* (schema-repair decay, rubric-critic score, validator pass ratio, citation coverage, source tier, tool success — the `wgm-v1` algebra of §5), citations that deep-link into the document viewer at page/bbox granularity, and scorecard arithmetic. Rendering those is the product; a generic span waterfall cannot do it.

## Decision

**The trace is first-party, in Postgres, with one write path serving explainability, debugging, and cost accounting simultaneously.**

| Table | One row per | Serves |
|---|---|---|
| `agent_steps` | lifecycle/LLM/tool event | step timeline, debugging, confidence breakdowns |
| `step_payloads` | gzipped prompt payload | full prompt replay without bloating hot rows |
| `evidence` | claim → source binding | citations, RAG grounding, audit ledger |
| `llm_calls` | gateway LLM call | cost accounting, cache/limiter diagnostics |

- **`agent_steps` (AgentStep rows)** — one row per lifecycle/LLM/tool event (`kind ∈ {plan, llm_call, tool_call, reflect, revise, final, error}`): run/node/agent/seq/attempt, model, output JSONB, tokens in/out, latency_ms, cost_usd, confidence score + full component dict, citations, error, timestamp. Full prompt payloads are gzipped into a side table (`step_payloads`, referenced by `prompt_ref`) so hot rows stay small.
- **`Evidence` and `llm_calls` tables** complete the triad: Evidence rows (claim, source ref, confidence, agent, timestamp) are simultaneously the RAG citation store and the audit ledger (§8); `llm_calls` (model, prompt-hash, tokens, latency, cost) is written by the single `LLMGateway` ([ADR-0005](ADR-0005-llm-gateway-and-model-tiering.md)).
- **`TraceWriter`** — injected into every `AgentContext` — buffers and batch-inserts rows, and simultaneously publishes compact events to Redis pub/sub channel `run:{id}:events`. FastAPI bridges that channel to SSE for the live run timeline; `GET /runs/{id}/trace` returns the ordered rows for replay. Redis pub/sub is live fan-out only and retains no backlog: Postgres is the source of truth, and an SSE reconnect (`Last-Event-ID`) replays the gap exclusively from the Postgres trace endpoint (ordered by AgentStep `seq`).
- **Redaction happens at write time, in `TraceWriter` — never in the UI.** Secrets, API keys, and sensitive identifiers (account numbers, PAN where the task allows) are stripped or masked before a row is persisted, so no consumer — SQL console, API, frontend — can see what was never stored (§15.6).
- **One consumer surface, two audiences:** the frontend "Explain" tab replays the same rows as a step timeline with expandable prompts/outputs, confidence-component breakdowns, and citation links into the document viewer. That view *is* the judge-facing explainability feature and *is* the developer's debugger.
- **Logs:** `structlog` JSON to stdout; `request_id`/`run_id`/`agent`/`org_id` bound via contextvars; Celery tasks inherit correlation IDs, so one `docker logs | grep run_id` follows a run across API and worker.
- **Metrics:** a first-party ~40-line middleware on `prometheus-client` (request counter + latency histogram labeled by method/route-template/status, exposed at `/metrics`) plus a Celery metrics exporter; Prometheus + Grafana (and Flower) ride behind the optional `--profile ops` compose profile — opt-in, never always-on overhead. `/health` checks DB, Redis, and ML model artifacts. (Phase-2 build note: `prometheus-fastapi-instrumentator` was the original pick but breaks against FastAPI ≥ 0.139 — router entries without `.path` crash its route-name lookup — so the platform owns those lines instead of pinning the framework down for a metrics helper.)
- **Cost is a product feature, not an ops afterthought:** per-call cost lands in `llm_calls`; per-case and per-tenant LLM spend is surfaced *in the product UI*, and cost dashboards are plain SQL.

## Alternatives considered

### LangSmith (managed SaaS)

Rejected on three independent grounds. First, data residency: prompts contain extracted bank-statement lines, PAN, GSTIN-keyed financials — shipping them to a US-hosted SaaS is a non-starter for the bank/NBFC buyer this platform is styled for, and undermines the DPDP posture documented in §15.6. Second, it reintroduces a network dependency on demo day; the architecture explicitly engineers that risk out (replay gateway, pre-warmed cache). Third, even if both were acceptable, LangSmith's trace UI cannot render confidence components, citation pins into a document viewer, or scorecard arithmetic — we would still build the explainability UI ourselves, leaving LangSmith as a second, divergent record of the same events.

### Langfuse v3 (self-hosted)

The honest self-hosted contender, and rejected with respect. Langfuse v3's architecture requires ClickHouse, a worker process, and S3-compatible blob storage alongside Postgres and Redis — three-plus new containers on a laptop governed by a two-stateful-services rule. That is a material operating cost (memory, startup ordering, backup story, Windows Docker quirks) for a solo developer whose trace *queries* are already answered by SQL. And the drift problem remains: Langfuse's generic observation model cannot serve the judge-facing UI, so the Evidence/AgentStep tables must exist anyway — Langfuse would be a duplicate write path, not a replacement. It may return later as a supplementary prompt-engineering console; it cannot be the system of record.

### Arize Phoenix (self-hosted, OTel-based)

The lightest third-party option — a single container, OpenInference semantics, local storage — and genuinely viable for generic LLM debugging. Rejected because it still cannot render our confidence-component/citation model (the part of the trace that carries audit value), it adds a container that duplicates what Postgres already stores, and its trace store is separate from the evidence ledger the *product* must query transactionally (findings superseded append-only, citations joined to chunks and documents). Two sources of truth for "what did the agent do" is precisely the failure mode this decision exists to prevent.

### Full OpenTelemetry + collector + Jaeger/Tempo

Right shape for cross-service latency analysis; wrong shape for this system. Spans are ephemeral operational telemetry with sampling and TTLs; our trace is a permanent, tenant-scoped audit artifact retained with the case and protected by the same RLS and retention policy as the rest of the ledger. A modular monolith on one host has no distributed-tracing problem to solve, and the collector + storage + UI chain is three more containers. If multi-host deployment ever arrives, OTel instrumentation can be added as an *export* layer over the same TraceWriter events without changing the system of record.

## Consequences

**Positive.**

- One write path: explainability, debugging, and cost accounting cannot drift apart because they are the same rows.
- The entire trace is queryable in SQL and joins transactionally to documents, chunks, findings, and scorecard breakdowns — "walk me through it" is literally `GET /runs/{id}/trace`.
- Nothing leaves the machine: the demo works offline, and the DPDP/data-residency story stays clean for the bank/NBFC buyer.
- The trace inherits tenant isolation (TenantContext + RLS, [ADR-0013](ADR-0013-auth-and-tenancy.md)) and the backup/migration story of the everything-store for free.
- The same recorded request/response data underpins the replay gateway (§5), so CI runs and demo-day fallbacks exercise the identical pipeline.
- Per-case spend as a UI feature turns cost governance (§15.7) into something a judge can see.

**Negative / accepted.**

- We own a trace UI that vendors give away — the step timeline with expandable payloads is real frontend work, and it will lag polished commercial tools in prompt-diffing and experiment comparison (the eval harness of §14 covers evaluation separately, as versioned artifacts, not dashboards).
- Trace writes add load to Postgres during parallel agent fan-out; `TraceWriter` batching and the gzipped payload side table are the designed mitigations, but connection-pool and batch-size tuning on laptop-grade hardware is our problem now.
- No sampling means the trace grows unbounded by default; retention is an obligation, not an option.
- There is no vendor to blame: redaction correctness is entirely on `TraceWriter`.

**Follow-up obligations.**
- Implement `TraceWriter` buffering/batch-insert and the `step_payloads` side table alongside the agent runtime (BaseAgent build phase); the live SSE bridge ships with the first end-to-end run.
- Unit-test redaction rules explicitly (account numbers, PAN, API keys) — a redaction regression is a data-protection incident, not a bug (§15.6).
- Add a retention/pruning job for `step_payloads` (and LangGraph checkpoints, which TTL separately) with a per-tenant retention policy as a first-class admin operation.
- Measure trace-write contention during parallel ingestion + fan-out in the integration test profile; tune batch sizes and pool limits before the demo hardening phase.
- Revisit Langfuse-as-supplement and OTel export only if multi-host deployment or team growth materialises — explicitly out of scope for the 10-phase roadmap.

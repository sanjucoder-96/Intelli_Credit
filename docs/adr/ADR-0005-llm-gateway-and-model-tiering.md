# ADR-0005: Single LLM gateway: Gemini 2.5 Pro/Flash tiering, cache, rate limiting, cost circuit breaker, replay mode

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit's call profile is sharply bimodal. The high-volume side — document classification, per-chunk domain extraction, OCR post-correction, search-query generation, rubric critics, the episodic Distiller — accounts for 80–90 % of all model calls and is schema-constrained work where a small model is sufficient. The low-volume side — cross-document financial synthesis, fraud-hypothesis reasoning, the risk narrative, the recommendation rationale, and CAM section writing — is precisely the artifact a judge or credit committee reads, and reasoning quality there *is* the product. A single credit case is 20–60 documents; ingestion and the parse fan-out (LangGraph `Send`) can emit dozens of concurrent extraction calls in a burst, so naive per-call clients would 429 against hackathon-tier Gemini quota mid-demo.

Three constraints bind the design. First, the solo-dev, docker-compose, Windows 11 posture (ARCHITECTURE.md §1): every mechanism must be debuggable by one person and add no new stateful service — Redis and Postgres are already running. Second, demo reliability: a live 10-minute end-to-end run must survive API flakiness, quota exhaustion, and venue-network failure. Third, bank-grade cost governance: a reflection-loop bug or fan-out mistake must be structurally incapable of producing an unbounded bill, and per-case LLM spend must be explainable — analysts and tenants see cost as a product feature, not a surprise on an invoice.

This ADR fixes where every model call flows, which model serves which step, and the mechanisms (cache, rate limiter, cost ledger, budget circuit breaker, record/replay) that make the LLM layer cheap, bounded, and reproducible. It is the enforcement point for product invariant #2: numbers come from deterministic code; the LLM only ever gets to produce words and classifications, and only through this one seam.

## Decision

All model traffic passes through **one `LLMGateway`** in `infrastructure/`, the sole client of the **google-genai SDK** (the current SDK, not the legacy `google-generativeai`). The `StructuredCaller` (schema enforcement + repair loops, ADR-0003/spec) rides on the gateway; agents receive it via `AgentContext` and never construct clients. Model IDs (`gemini-2.5-flash`, `gemini-2.5-pro`) are pinned in `pydantic-settings` config so a model deprecation is a config change, not a code hunt.

- **Tiering (static per node type, declared in `AgentSpec.model_policy`):**
  - **Flash** — classification, per-chunk extraction, OCR verification/post-correction, query generation, news triage, tool-argument synthesis, all rubric critics, distillation. ~4× cheaper per token (and lower latency) than Pro, which is what makes per-document fan-out affordable — and since Pro's thinking tokens bill as output, the realized per-call cost gap on synthesis nodes is typically larger than the per-token ratio.
  - **Pro, with an explicit `thinking_budget`** — cross-document synthesis, fraud reasoning, risk narrative, recommendation rationale, CAM section writing, report writer. Low-volume, high-stakes.
- **Escalation:** confidence-triggered, **one-shot, Flash→Pro only** — a Flash step scoring below its `confidence_floor` (default 0.6) after retries, or exhausting its schema-repair budget, reruns once on Pro. No Pro→Pro loops; a Pro step that still scores low is flagged `low_confidence` and routes to HITL per the retry ladder.
- **Response cache:** Redis, key = `(model, SHA-256 of the canonicalised request)` — system prompt, contents, tool declarations, `response_schema`, and generation config all enter the hash, so a prompt-pack edit is automatically a cache miss. `make demo-seed` pre-warms the cache along the demo path.
- **Rate limiting:** a **global token-bucket in Redis** (not a per-process semaphore — worker parallelism spans multiple `solo`-pool containers on Windows), sized to the *measured* quota tier of the actual API key. Fan-out concurrency queues at the bucket instead of surfacing 429s; transient 429s that do occur follow the standard backoff-with-jitter policy.
- **Cost accounting:** every call writes an `llm_calls` row (model, prompt hash, tokens in/out, latency, USD cost, run/agent/tenant correlation). Cost dashboards are SQL; per-run and per-tenant roll-ups are surfaced in the product UI.
- **Budget circuit breaker:** a hard per-run USD cap lives **in graph state** (`CostLedger`, decremented by `BudgetMeter`) — a circuit breaker, not a ledger. On exhaustion, escalation disables and the run *completes* on Flash with warnings attached to affected findings; per-agent `StepBudget` caps (`max_llm_calls`, `max_cost_usd`) bound any single node. A demo never dies from quota; a runaway loop can never become an unbounded bill.
- **Record/replay:** in record mode the gateway persists request-hash → full response, including streaming chunk boundaries. Replay mode serves a recorded run **byte-identically through the real UI streaming path** (Redis pub/sub → SSE), and a hash miss in replay is a typed failure — so CI runs the full agent layer with zero API keys and catches silent prompt drift, and the same switch is the demo-day offline fallback.
- **Schema-subset discipline:** Gemini's `response_schema` supports a JSON-Schema subset (depth/feature limits). Deeply nested outputs — the full CAM — are generated **per section** against flat section schemas and assembled by the report writer. CI carries **Gemini schema-compatibility contract tests for all 12 agent output schemas**, so an incompatible schema fails the build, not the demo.

## Alternatives considered

### Gemini 2.5 Pro everywhere
Rejected on cost and latency, not capability. Per-chunk extraction over a 60-document case multiplies every token by the fan-out; at roughly 4× Flash's per-token price (and lower latency for Flash) — with Pro's thinking tokens billing as output, so the realized per-call gap is typically larger than the per-token ratio, Pro-everywhere makes ingestion sluggish, burns hackathon-tier quota mid-demo, and buys nothing — extraction calls are schema-constrained, and the deterministic post-validators (running-balance continuity, GSTR-3B arithmetic) are what guarantee correctness there, not model size.

### Gemini 2.5 Flash everywhere
Rejected because the judged artifact suffers visibly. CAM prose, the Five Cs narrative, and fraud-hypothesis reasoning are exactly where Flash is measurably weaker, and they are low-volume — Pro on those nodes costs cents per run. Flash-everywhere optimises the 90 % of calls that were already cheap and degrades the 10 % that constitute the product. It survives only as the *degraded* mode behind the budget circuit breaker, with warnings.

### Multi-provider routing (OpenAI/Anthropic fallback, LiteLLM-style proxy)
Rejected for this build (also rejected at the architecture level, ARCHITECTURE.md §2). A second provider doubles the prompt-tuning and eval surface for a solo developer — every prompt, schema quirk, and repair-loop behaviour would need validation twice — and an off-the-shelf proxy adds a routing layer whose failure modes we would still own. The actual risk multi-provider claims to mitigate is demo-day API failure, and the cache + replay gateway addresses that directly and deterministically. The single seam means a future provider swap is one adapter, accepted as a consciously deferred option rather than pre-paid complexity.

### Dynamic router model (per-call model selection by a classifier)
Rejected because the routing decision is static per node type: extraction is always Flash, CAM writing is always Pro. A learned or LLM-based router adds latency and a new failure mode to every call to re-derive a decision already encoded in `AgentSpec.model_policy`. The one genuinely dynamic case — Flash underperforming on a specific input — is handled by confidence-triggered escalation, which is measured (confidence components) rather than predicted.

### Local LLMs (Ollama/llama.cpp/vLLM)
Rejected on quality and hardware. Open-weight models at laptop-runnable sizes are not competitive on messy Indian financial documents (GSTR forms, scanned bank statements, CIBIL reports) or long cross-document synthesis, and a GPU-dependent serving stack contradicts the CPU-only Windows 11 docker-compose constraint. Offline operation — the one thing local models genuinely offer — is delivered by the replay gateway and pre-warmed cache instead.

## Consequences

**Positive.** One choke point gives uniform caching, rate limiting, retries, cost accounting, tracing, and redaction for free to all 12 agents — no agent can bypass governance. Cost is bounded by construction (budget in state) and visible per run, per agent, per tenant in `llm_calls`. CI exercises the real agent layer without keys or spend; the demo has a deterministic offline mode through the genuine UI path, so what CI protects is literally what the judges see. Tiering keeps a full credit-appraisal run in the tens-of-cents range, which is itself a demoable engineering feature.

**Negative / accepted.** Single-provider dependency: a Gemini outage or behavioural drift degrades the whole platform until prompts are re-tuned behind the gateway — accepted; the mitigation budget went to cache + replay instead of a second provider. Budget-exhausted runs complete on Flash with honestly lower confidence, pushing more nodes to HITL — degraded-but-explainable beats failed. Replay fixtures and the recorded demo run are artifacts that go stale with every prompt-pack change and must be re-recorded. The cache serves stale responses if upstream model behaviour changes under an unchanged request hash — tolerable for a cache whose purpose is determinism.

**Follow-up obligations.**
- Early integration spike (agent-runtime phase): measure the real quota tier and size the token bucket; decide whether Flash and Pro get separate buckets.
- Early spike on `response_schema` subset limits against the full CAM section schemas; the CI contract tests land with the first agent schemas.
- Re-record replay fixtures and re-warm the demo cache as part of `make demo-seed` on every prompt/schema version bump (release checklist item).
- Per-tenant spend roll-ups and alerting thresholds ride on `llm_calls` (portfolio-monitoring phase).
- Revisit multi-provider only if Gemini pricing/deprecation forces it; the gateway seam is the designed insertion point.

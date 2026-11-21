# ADR-0003: LangGraph for orchestration only; no LangChain chains; no Pydantic-AI

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit runs twelve workflows (credit appraisal, vendor risk, ESG, underwriting, fraud detection, …) as agent graphs over a shared pool of twelve agents. Three requirements are non-negotiable and shape this decision. First, **human-in-the-loop is a hard product requirement**: the credit-officer review gate ("factory at 40 % capacity" becomes a primary finding that adjusts the risk score with an auditable delta) means a run must pause mid-graph — possibly for days — and resume exactly where it stopped. Second, **crash resumability**: runs execute inside Celery tasks on a Windows 11 docker-compose laptop; a worker restart mid-run must not lose an hour of OCR, research, and LLM spend, and a retried task must never re-execute completed side-effecting nodes. Third, the `analyze` stage fans five agents out in parallel per case (and document parsing fans out per document), so the orchestrator needs first-class parallel branches whose state merges safely.

The prescribed hackathon stack names both LangChain and Pydantic-AI alongside LangGraph. This project is built by one developer who must debug every layer personally; the architecture's governing rule is that every component must be already running or earn a new box — and the same rule applies to frameworks: every abstraction must earn its stack frames. Two agent runtimes with overlapping responsibilities is precisely the kind of cost that is invisible in a demo and brutal in week six.

A further force: all structured LLM output must feed the confidence algebra (schema-repair decay is a scored confidence component), the per-run USD budget circuit breaker, and the first-party trace (`agent_steps` rows) — so the LLM-calling seam must be code we own, not behavior buried inside a framework's retry internals.

## Decision

**LangGraph is the only orchestration framework, used strictly *between* agents; all model calls go through one owned `StructuredCaller` on the google-genai SDK; Pydantic-AI and LangChain's chain/agent layers are not adopted.**

- **LangGraph `StateGraph`, compiled — never hand-built.** The `GraphCompiler` (`compile_workflow()`) is the only code that touches LangGraph's builder API; it compiles immutable versioned `WorkflowDefinition`s (YAML → Postgres) into graphs cached per `(id, version)` (ADR-0008). No other module imports the builder.
- **Durable checkpointing:** `langgraph-checkpoint-postgres` (`AsyncPostgresSaver`) in the main PostgreSQL 17 instance, dedicated schema, `thread_id = run_id`. Zero new infrastructure (ADR-0002); checkpoints survive container restarts; a TTL job prunes checkpoints for completed runs (our own trace tables remain — they are the audit record, not the checkpoint).
- **HITL via `interrupt()`:** the `human_review` node raises a typed `ReviewRequest` (summary, officer questions, draft findings, form-schema ref). Resumption is `POST /runs/{id}/hitl/{stage}` → `graph.ainvoke(Command(resume=ReviewResponse), config={"configurable": {"thread_id": run_id}})`. A pause of days costs nothing — it is a Postgres row.
- **Crash resumability:** one Celery task owns one graph invocation. On crash, Celery's retry re-invokes with `input=None` and the same `thread_id`; LangGraph resumes from the last checkpoint and **checkpointed supersteps never re-execute** (§15.3 of ARCHITECTURE.md) — but resumption is checkpoint-granular, not statement-granular: on `Command(resume)` the interrupted node re-executes from its start, and a mid-node crash re-runs that node's already-performed calls. **Node-design rule:** `interrupt()` must be the first meaningful statement of a `human_review` node (no side effects before it), and node-internal re-execution is absorbed by the Redis prompt-hash cache plus idempotent writes. Worker runs the `solo`/`threads` pool per container — Windows-safe, clean asyncio loop.
- **Parallel fan-out:** LangGraph `Send` API for per-document parsing fan-out and the five-way `analyze` stage; append-only state channels (`findings`, `citations`, `errors`) use `operator.add` reducers so parallel branches merge without races.
- **Two-level execution model:** LangGraph orchestrates between agents; *inside* a node each agent runs a bounded micro-loop in plain async Python — plan → act (tool loop + structured synthesis) → reflect → revise (at most once) → score confidence. **No nested subgraphs**: a bounded loop is easier to test with a fake gateway, cheaper to checkpoint, and its trace reads linearly in the explainability UI.
- **One LLM seam:** every model call goes through `StructuredCaller` wrapping the **google-genai SDK** directly — `response_mime_type="application/json"` + native `response_schema`, then Pydantic re-validation (cross-field rules and `Evidenced[T]` semantics that server-side enforcement cannot check), then a bounded repair loop (re-prompt with the offending JSON + formatted errors, max 2), then one regeneration on the escalated model, then a typed `UnrepairableOutput`. Every attempt writes a trace row and decrements the `BudgetMeter`. Deeply nested schemas (the full CAM) generate per-section and assemble, respecting Gemini's response-schema subset limits.
- **`langchain-core` is present only as LangGraph's transitive dependency.** No Chains, no `AgentExecutor`, no output parsers, no LangChain tool wrappers; tools are our own `ToolSpec`s exposed via Gemini function calling.

## Alternatives considered

### Pydantic-AI (the prescribed-stack item, deliberately dropped)

Pydantic-AI's core value — schema-validated outputs with retry, typed tool calling — duplicates roughly 200 lines we own once in `StructuredCaller`, and those 200 lines are not incidental: the repair loop must decay the `schema` confidence component (0.85^attempts), charge the budget meter, and emit trace rows per attempt, none of which a third-party retry loop exposes without fighting it. Worse, Pydantic-AI ships its own agent abstraction (its `Agent`, run loop, and tool model), which competes head-on with LangGraph's node/state model: adopting both means two runtimes to debug, two mental models of "what is an agent", and an impedance mismatch wherever a Pydantic-AI agent is wrapped inside a LangGraph node. And it offers no path to the features this platform genuinely cannot do without — durable Postgres checkpointing, `interrupt()`-based HITL, `Send` fan-out, crash resumability. We would carry a second framework for capabilities we already own and still depend on LangGraph for everything hard. One orchestration framework, one thin model client, Pydantic everywhere.

### LangChain chains / AgentExecutor

`AgentExecutor` is a deprecated pattern by LangChain's own guidance (superseded by LangGraph), and chains/output parsers add indirection without capability: our calls are direct SDK invocations with native `response_schema` — a chain around that is a wrapper around a wrapper. The operating cost is real: LangChain's abstraction churn means every minor upgrade risks breaking prompt plumbing a solo developer must then re-verify across twelve agents, and opaque internal loops are exactly where an unbounded retry burns quota mid-demo. Failure mode observed across the ecosystem: debugging a misbehaving chain means stepping through framework internals instead of one owned function.

### CrewAI / AutoGen

Both optimize for conversational multi-agent collaboration and neither has a durable-state story that meets our requirements: no Postgres-backed checkpointing that survives a worker crash, no first-class interrupt/resume primitive that can hold a run open for days awaiting a credit officer, and no deterministic, auditable topology — their emergent agent-to-agent chatter is the opposite of "the run is replayable from the database" (product invariant #1). Bank-grade explainability needs a fixed, versioned DAG, not a negotiation transcript.

### Hand-rolled graph engine

A bespoke executor looks like a weekend of work until it needs everything LangGraph already has: checkpoint serialization of arbitrary state, resume-without-re-execution semantics, interrupt payload plumbing, parallel branch merge with reducers, and the subtle interaction of all four under a Celery retry. Re-implementing durable checkpointing badly is the single most likely way to corrupt the append-only evidence ledger the platform sells. LangGraph's checkpointer is battle-tested and its scope here is deliberately narrow — confined behind `compile_workflow()`, it is replaceable if that ever changes.

### Per-agent nested LangGraph subgraphs

Making each agent's plan/act/reflect loop a subgraph checkpoints every micro-step, bloating the checkpoint store for no recovery benefit (an agent step is cheap to redo; a whole run is not), makes unit testing require graph scaffolding instead of calling `agent.run(ctx)` with fakes, and doubles the surface where LangGraph API changes bite. The micro-loop is bounded by construction (max one revision) — it does not need durable orchestration.

## Consequences

**Positive.** One framework to learn, pin, and debug; LangGraph is used only where it is irreplaceable, so its blast radius is one compiler function. HITL pauses and worker crashes are the same mechanism (checkpoint + resume), tested once. The `StructuredCaller` seam makes agents deterministic under test (`FakeLLMGateway`, replay fixtures — CI runs with no API keys) and gives the confidence algebra, budget circuit breaker, and trace one enforcement point. Checkpoints ride the existing Postgres backup; nothing new runs on the laptop.

**Negative / accepted.** We own the structured-output client: repair loop, Gemini function-calling tool loop, schema-nesting workarounds are ours to maintain and test (~200 lines plus contract tests for all 12 agent output schemas — accepted as cheaper than a second framework). We are coupled to LangGraph's API evolution and to `langchain-core` as a transitive dependency; versions are pinned and upgrades are deliberate. Checkpoint tables grow with active runs and need the TTL pruning job. The "references, not blobs" state rule must be enforced in review — a careless node stuffing parsed documents into `RunState` bloats every checkpoint write.

**Follow-up obligations.**
- Integration spike: `langgraph-checkpoint-postgres` + Celery `solo` pool under docker-compose on Windows 11 (event-loop lifecycle per task, connection-pool reuse across resumes); pin exact versions on its outcome; fall back to the `threads` pool if `solo` throughput is insufficient (Phase 2, before agent-runtime build).
- Early spike on Gemini `response_schema` limits for the full CAM model; confirm per-section generation and assembly in the Report Writer (Phase 5).
- Global async token-bucket in the `LLMGateway` (Redis-backed, per ADR-0005; `StructuredCaller` rides on the gateway) sized to the actual Gemini quota tier, so `Send` fan-out cannot trip rate limits mid-demo (Phase 5).
- Checkpoint TTL pruning job for completed runs (Celery beat) with traces retained (Phase 5).
- Chaos test in CI: kill the worker mid-run, assert resume with zero re-executed supersteps and an intact append-only ledger (Phase 8, with the idempotency suite of §15.3).
- Freeze the `GraphCompiler` DSL only after credit appraisal, vendor risk, and ESG have shaped it; any `custom_node` escape hatch used twice becomes a DSL feature (Phase 8, at the three-workflow checkpoint).

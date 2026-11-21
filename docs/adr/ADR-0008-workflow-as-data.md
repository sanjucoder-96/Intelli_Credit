# ADR-0008: Workflows as data: YAML definitions, immutable versions, one GraphCompiler, server-driven dashboards

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit ships twelve workflows at launch — credit appraisal, vendor risk, ESG, underwriting, fraud detection, and seven more — under a hard product requirement: **workflow #13 must be a configuration exercise, not a code fork**. A future hackathon vertical (a new compliance regime, a new document pack, a new scoring policy) has to ship in days. For a solo developer, twelve hand-written pipelines would drift into twelve forks; the platform's core asset is the mechanism that prevents that.

Bank-grade explainability adds a second force: a credit decision is challenged months or years later. When an RBI inspector or an internal credit committee asks "what policy did this borrower's case actually run against?", the answer must be a database query, not archaeology. That means every run must be pinned to the *exact* workflow definition it executed — a case decided in 2026 stays explainable against `credit_appraisal@3` forever, even after v4 ships. A workflow is also more than topology: it binds document requirements (GSTR-3B, bank statements, CIBIL Commercial reports), per-agent tool whitelists and spend budgets, HITL gates, scorecard and policy-grid versions, report templates, and the dashboard the analyst sees. All of that must vary per vertical without touching core code — including the frontend, which must never grow a per-workflow page.

The operating constraints are fixed elsewhere: LangGraph is the orchestration runtime (ADR-0003), Postgres is the everything-store (ADR-0002), and the whole system runs via docker-compose on a Windows 11 laptop. This ADR decides how workflows are authored, validated, versioned, compiled, and rendered.

## Decision

**A workflow is data.** Concretely:

- **Authoring:** `WorkflowDefinition`s are YAML files in git (`backend/app/workflows/definitions/*.yaml`, loaded with PyYAML `safe_load`). Git gives review, diff, blame, and history for free — a workflow change is a pull request, not a database mutation.
- **Validation:** at registration (the `RegisterWorkflow` use case), the YAML is parsed into a Pydantic v2 `WorkflowDefinition` model and validated fail-fast: every referenced agent, tool, schema, predicate, scorecard, template, and document type must resolve against its registry, budgets must be positive, the stage graph must be a DAG. A definition that references anything unregistered is rejected before it can ever run.
- **Persistence:** the validated definition is persisted to a Postgres `workflow_definitions` table as an **immutable numbered version** (`id`, `version`, content, content hash). Re-registering an existing `(id, version)` with a different content hash is an error — versions are minted, never edited. Runtime enablement per tenant is a feature flag (ADR-0015), separate from the definition itself.
- **Pinning:** every `WorkflowRun` records the `(id, version)` it executed. Replay, the evidence trail, and the "Explain" tab always resolve against the pinned version.
- **One compiler:** the `GraphCompiler` is the **only code that touches LangGraph's builder API**. It compiles a definition version into a `StateGraph`: sequential stages; parallel fan-out (`parallel:` lists and per-document `Send`); conditional edges bound to **named predicates from the `PredicateRegistry`** evaluated over typed run state — declarative references, never `eval`'d code; `interrupt()` gates for human review; document-checklist ingest gates (`require_documents`); and per-agent **tool whitelists and budgets** — a compliance agent whose definition omits `web_search` physically cannot call the web. Compiled graphs are cached per `(id, version)`. The shape, abbreviated from the reference definition (ARCHITECTURE.md §4):

  ```yaml
  id: credit_appraisal
  version: 3
  stages:
    - id: ingest_gate
      require_documents: [gstr3b, bank_statement]
    - id: analyze
      parallel: [financial_analysis, research, news, compliance, fraud_detection]
    - id: human_review
      interrupt: primary_dd_notes
      when: "confidence.research < 0.75 or findings.severity >= HIGH"
    - id: score
      scorecard: credit_appraisal@2          # deterministic scorecard version (ADR-0009)
      ml_models: [pd_xgboost@champion]
    - id: decide
      policy_grid: credit_pricing@1          # grade→rate band, MPBF/turnover limit ceilings
  agents:
    research:
      tools: [web_search, web_extract, registry_lookup]
      budget: {max_tool_calls: 25, max_llm_calls: 15, max_cost_usd: 0.75}
  dashboard:
    blocks:                                 # typed instances of the fixed block vocabulary
      - {type: risk_gauge}
      - {type: table, id: five_cs}
      - {type: timeline, id: cashflow}
      - {type: network, id: promoter_graph}
      - {type: evidence_chain}
  ```
- **Nine registries are the entire extension surface:** `AgentRegistry`, `ToolRegistry`, `SchemaRegistry`, `PredicateRegistry`, `DocumentTypeRegistry`, `FraudCheckRegistry`, `ScorecardRegistry`, `TemplateRegistry`, and the ML `ModelRegistry`. Adding workflow #13 = one YAML + one Pydantic output schema + one prompt pack + one Jinja2 template + one scorecard config + optionally one new registration (`@AgentRegistry.register(...)` or a document-type plugin module). No routing changes, no frontend fork.
- **Server-driven dashboards:** the frontend fetches `GET /workflows/{id}/manifest` and renders each workflow's dashboard from a **fixed block vocabulary** — `risk_gauge`, `table`, `timeline`, `heatmap`, `network`, `narrative`, `evidence_chain` — all rendered by the existing ECharts/viewer components (ADR-0014). New workflow, same blocks, different data. The manifest is derived from the definition's `dashboard.blocks` list, so it is versioned with everything else.
- **Multi-source ingestion is a port, not a rewrite:** a `SourceConnector` port (`pull(since) -> list[RawDocument]`) abstracts evidence origin. Local upload is connector #1; a Databricks/lakehouse table reader, an S3 bucket watcher, or a hackathon-provided API is a new connector class registered like any other plugin.
- **Discipline rules:** (1) *harvest the abstraction* — credit appraisal is built end-to-end concretely first; vendor risk and ESG follow; the DSL is frozen only after those three real workflows have shaped it, and all 12 launch workflows must be authored as pure YAML before the mechanism is declared done. (2) *The escape hatch is rationed* — a stage may name a Python `custom_node` for exotic control flow, but any escape hatch used twice becomes a DSL feature; bespoke nodes must never quietly hollow out "configuration, not code".

## Alternatives considered

### Workflow-as-code: a Python module or subclass per vertical
The obvious first move — each workflow is a class wiring its own LangGraph — and the one the requirements forbid. Twelve modules calling the builder API drift independently: a checkpointing fix or a budget-enforcement change must be re-applied twelve times, and workflow #13 is by construction a code fork. It also erases the audit property: code has git history, but a *run* cannot cheaply prove which code path it executed, whereas a pinned `(id, version)` row can. Rejected on the hard requirement, not on taste.

### A BPMN/Temporal-class workflow engine (Camunda, Temporal, Zeebe)
These engines solve durable execution superbly — and are an entire orchestration platform to deploy, learn, and babysit on a laptop that already runs seven containers. Temporal alone adds its server, its database usage, and a second workflow programming model alongside LangGraph, whose Postgres checkpointing, `interrupt()`, and `Send` fan-out already cover resume, HITL, and parallelism (ADR-0003). Worse, BPMN's vocabulary cannot express agent semantics — confidence-gated routing, reflection, tool budgets — without escape hatches everywhere, which is the failure mode this ADR exists to prevent. A second engine is operating cost with no capability gain.

### Database-only definitions (admin-UI authored, no git)
Storing definitions solely as DB rows makes runtime registration trivial but loses everything git provides: code review before a policy change goes live, meaningful diffs between `credit_appraisal@2` and `@3`, and disaster recovery of the authoring history. For a credit platform, "who changed the scoring stage and who reviewed it" is a compliance question. We keep the DB as the immutable *execution* record and git as the *authoring* record — deliberately both, joined by the registration step.

### Dynamic LLM-planned graph topologies
Letting a planner model assemble the graph per case is fashionable and fatal here: non-deterministic topology means two identical cases can take different paths, which kills auditability ("why did fraud detection not run?" has no policy-level answer), makes cost unbounded, and turns demo reliability into a dice roll. Product invariant #1 requires the *topology* to be policy. LLM planning is confined to where autonomy pays — the budgeted research supervisor *inside* a node (§6 of ARCHITECTURE.md) — never the graph itself.

### Hand-built dashboards per workflow
Bespoke pages per vertical produce the best-looking single demo and an O(n) frontend: twelve dashboards to build, theme, and maintain, and workflow #13 waits on frontend work — violating "registration, not a fork" from the other side of the API. The fixed block vocabulary caps expressiveness (accepted below) but makes the frontend O(1) in the number of workflows.

## Consequences

**Positive.**

- Workflow #13 is genuinely a configuration exercise, and the mechanism is exercised twelve times before any judge asks.
- Every run is forever explainable against its pinned definition — version pinning plus append-only findings (ADR-0002, §15.4) makes the 2026-case audit a database query.
- Policy changes are reviewable pull requests with meaningful diffs; "who changed the scoring stage and who approved it" has a git answer.
- The frontend never changes per workflow; frontend cost is O(1) in the number of verticals.
- Tool whitelists and budgets are enforced by construction at compile time — a definition is simultaneously a security document (prompt-injection blast radius, §15.1) and a cost-containment document (§15.7).
- Confining the builder API to one compiler means LangGraph upgrades and orchestration fixes land in exactly one place; compiled-graph caching keeps run startup cheap.

**Negative / accepted trade-offs.**

- The DSL is a real product surface: its Pydantic schema must itself be versioned and migrated (Alembic from day one; explicit version columns per ARCHITECTURE.md §14).
- There is an expressiveness ceiling — a future vertical may demand control flow the stage/parallel/predicate/interrupt vocabulary cannot express. `custom_node` prevents a fork but risks accumulating bespoke Python unless the used-twice rule is actually enforced.
- Definitions live in two places (git and Postgres); drift between HEAD and registered rows is possible. Mitigated: registration is the only write path, content hashes make versions immutable, and CI checks that registered versions match their git source.
- The block vocabulary constrains dashboard creativity: a genuinely new visual is a platform change (new block type in the manifest schema and the frontend), deliberately expensive.
- The GraphCompiler concentrates complexity — a compiler bug affects all twelve workflows — so it needs compile-snapshot tests per definition, not just unit tests.
- Immutability means even trivial edits mint new versions; version churn is the accepted price of the audit story.

**Follow-up obligations.**

1. Build credit appraisal concretely end-to-end first, then vendor risk and ESG; freeze the DSL only after those three have shaped it (early build phases per ROADMAP.md).
2. Author all 12 launch workflows as pure YAML before declaring the mechanism done — the acceptance gate for the workflow-engine phase.
3. Ship the `RegisterWorkflow` use case with content-hash immutability enforcement plus the CI git↔DB consistency check.
4. Implement `GET /workflows/{id}/manifest` and the seven block renderers in the frontend phase.
5. Add an escape-hatch audit: count `custom_node` references per release; any hatch used twice opens a DSL-feature ticket.
6. Verify that a run interrupted for HITL resumes correctly against its pinned version even after later versions register — an explicit integration test at the Celery × LangGraph seam (§15.3).

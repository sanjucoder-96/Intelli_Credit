# ADR-0015: Feature flags: Postgres table + Redis cache + typed accessor

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit needs runtime toggles as a first-class platform capability, not a convenience. Four concrete forces demand them: (1) **workflow availability is per-tenant** — a bank org may license credit appraisal and fraud detection while an insurer org gets underwriting and ESG; gating `workflow.esg_analysis` per `org_id` is a product feature, not ops plumbing. (2) **Risky agent features must ship dark**: listwise reranking is explicitly feature-flagged default-off (ARCHITECTURE.md §8), and reflection depth and model-version pinning need the same kill-switch treatment so a regression in an agent capability is a toggle away from rollback, not a redeploy. (3) **Demo variants** — the `fast_path` run configuration and pre-warmed-cache demo mode (§16) — must be switchable live, mid-demo, without restarting containers. (4) **WorkflowDefinitions reference flags by name**, so flag resolution sits on the run-execution hot path and must be cheap and deterministic.

The operating constraints are the same ones that shaped ADR-0002: one developer, Windows 11, `docker compose up`, exactly two stateful services (PostgreSQL 17, Redis 7), and a live demo that must not depend on any external network call. Bank-grade explainability adds a quieter requirement: when a case decided in March is reviewed in June, "was reranking on for that run?" must be answerable — flag state must be durable, audited, and included in the same backup as the runs it influenced.

The prescribed stack suggests a dedicated flag service (Unleash or similar). This project deliberately simplifies the prescribed stack wherever a component would be a new box serving one node; feature flags are the clearest instance of that rule, so the deviation is argued in full below. The realistic flag inventory is ~15 flags across four namespaces — far below the threshold where a management platform pays for its own operation.

## Decision

A **`feature_flags` Postgres table, read through a short-TTL Redis cache, behind one typed accessor** — approximately 100 lines of owned code (see ADR-0002 for Postgres as the everything-store).

- **Table:** `feature_flags(id, name, scope, scope_ref, enabled, value_json, updated_by, updated_at)` with `scope ∈ {global, org, workflow}` and `scope_ref` holding the `org_id` or workflow id for scoped rows. `UNIQUE(name, scope, scope_ref)`. Managed by Alembic like every other table; `value_json` allows non-boolean payloads (e.g. a pinned model version) without a second mechanism.
- **Typed accessor:** `flags.is_on("workflow.esg_analysis", org)` (and `flags.value(name, org, default=...)` for JSON payloads), injected via `api/providers.py` and available to agents through `AgentContext` — no module-level singletons, so tests inject a `FakeFlags`.
- **Resolution precedence**, most specific wins: environment-variable override (`IC_FLAG_<NAME>`, dots → double underscores, parsed by `pydantic-settings`) → org-scoped row → workflow-scoped row → global row → **code-declared default (off)**. Unknown flag names resolve to off and log a warning — fail-safe, never fail-open.
- **Caching:** read-through Redis with a 30-second TTL, one key per `(name, scope, scope_ref)`. Writes go to Postgres first, then delete the affected keys; the TTL bounds staleness even if invalidation is missed. Redis-down degrades to direct Postgres reads (flags are a few-row indexed lookup), never to an exception on the run path.
- **Namespaces (enforced by the accessor):** `workflow.*` (availability per org), `model.*` (version pinning / candidate trials, cross-referenced against the ML `model_registry`), `agent.*` (reflection depth, reranking, budget experiments), `demo.*` (fast-path variants, replay mode).
- **Validation at the seams:** workflow registration (ADR-0008) validates that every flag name referenced in a `WorkflowDefinition` exists in the table or is declared with a default — a typo'd flag name fails registration, not a production run.
- **Mutation surface:** admin-role REST endpoints (`GET/PUT /admin/flags`), writing an audit-log row per change (ADR-0013's audit posture) — `updated_by`/`updated_at` make "who turned reranking on and when" a query.
- **Durability:** flag state lives in the same database as runs and evidence, so `pg_dump` captures the platform's entire behavioural configuration in one backup, and `make demo-seed` seeds demo-org flags alongside demo data.

## Alternatives considered

### Unleash / Flagsmith (self-hosted)

The prescribed-stack option. Unleash brings a dedicated API server, an admin UI, its own Postgres schema, and an SDK with local evaluation — genuinely valuable at fleet scale with gradual rollouts and many teams. Here it is a **third stateful-ish service plus an admin UI to run, upgrade, secure, and back up for one node and ~15 flags**. Its failure modes are all new: SDK polling against a dead Unleash container stalls flag reads at worker boot; its data lives outside the one-backup story, so "what was the flag state for run X" now spans two systems; and the admin UI needs its own auth story next to ours. Every capability we actually need — boolean/JSON values, org scoping, audit trail — is a table we already know how to operate. Rejected on operating cost.

### LaunchDarkly (SaaS)

Best-in-class product, wrong constraints. It is an external network dependency on the run path of a platform whose demo must survive a venue with no reliable internet (the same reason the replay gateway exists, §5). It introduces recurring cost for a portfolio project, places tenant-shaped metadata (org identifiers) in a third-party system a bank/NBFC buyer would question under DPDP review, and its streaming SDK is one more long-lived connection to reason about inside Celery workers. Rejected: a demo that dies because a flag SDK cannot reach its CDN is an unforced error.

### Environment variables only

Zero new code and the twelve-factor reflex — but env vars fail three of the four forces outright. They cannot express **per-org** targeting (workflow licensing per tenant is a product requirement, not ops config); changing one requires container recreation, so there is **no runtime toggling** — unacceptable mid-demo and antithetical to a kill switch; and they leave no audit trail of who changed what when, which breaks the "flag state is part of the explainability record" requirement. Env vars survive in this design in their correct role: a local-dev **override** layer on top of the table, not the store.

### Boolean columns on the org/workflow tables

Ad-hoc columns (`orgs.esg_enabled`, …) avoid a new table but couple every new toggle to an Alembic migration, scatter flag reads across the codebase with no single accessor, precedence model, cache, or audit path, and make a global kill switch a multi-table update. This is how flag systems accrete by accident; rejected in favour of paying the ~100 lines once.

## Consequences

**Positive.**

- Runtime and per-org toggling with zero new services; flag reads are Redis-cached and add no measurable latency to run execution.
- Flag state ships inside the one Postgres backup and is queryable next to the runs it governed — "was reranking on for run 4711" is SQL, which is exactly the explainability posture the rest of the platform sells.
- The env-override layer keeps local dev friction-free: `IC_FLAG_AGENT__RERANK=true` without touching the database.
- The typed accessor plus registration-time name validation makes flags greppable, refactorable, and typo-proof.
- Demo-day switches (`demo.fast_path`, replay mode) are one authenticated PUT away, live, mid-demo.

**Negative / accepted trade-offs.**

- No percentage rollouts, cohort targeting, or A/B experimentation. If gradual rollout is ever genuinely needed, that is the trigger to revisit Unleash — not to grow this table into a platform.
- No polished flag-management UI at first; the admin endpoints (and psql) are the interface until an admin screen ships alongside the tenant-admin views.
- Up to 30 seconds of cache staleness after a change. Acceptable for kill switches; documented so nobody expects instant propagation.
- Flag hygiene is manual discipline: nothing forces dead flags out of the code. The inventory is reviewed at each phase gate and stale flags are deleted, not accumulated.
- ~100 lines of owned code is still owned code: the precedence logic and cache invalidation need their own unit tests (table-driven, against a fake Redis).

**Follow-up obligations.**

1. Implement table + accessor + env override with the platform skeleton and wire `AgentContext` exposure — Phase 3–4 — the `feature_flags` table/schema with the Phase-3 database work, the typed accessor + env override with the Phase-4 feature-flag service (ADR-0013), since org scoping depends on `TenantContext`.
2. Enforce registration-time flag-name validation inside the WorkflowRegistry when workflow-as-data lands (ADR-0008 phase).
3. Ship the admin flag endpoints + audit rows with the credit-officer/admin portal work.
4. Seed demo-org flags in `make demo-seed` and pin the demo-path flag state in the Playwright smoke, so CI catches any flag drift that would break the judged demo.
5. Revisit this ADR if any of the following materialise: multi-node deployment (cross-host cache invalidation), a flag inventory above ~50, or a percentage-rollout requirement.

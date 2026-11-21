# ADR-0013: Auth & tenancy — first-party JWT, repository TenantContext, and Postgres RLS as a second isolation layer

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit is multi-tenant by design: banks, NBFCs, insurers, and audit firms each operate as an organisation whose cases, documents, findings, and reports must be invisible to every other organisation. The data is about as sensitive as SaaS data gets — bank statements with account numbers, PAN and GSTIN identifiers, CIBIL Commercial reports, draft credit decisions. A cross-tenant leak is not a bug here; it is the end of the product's credibility with exactly the buyer (a bank's risk function, mindful of RBI outsourcing guidelines and India's DPDP Act) the platform is pitched at. At the same time, the operating constraints are unforgiving: one developer, Windows 11, `docker compose up`, and a hard rule that every component must be already running or earn a new box (ADR-0001). An identity server is the classic "enterprise checkbox" that violates that rule.

Authorisation is also a product feature, not plumbing. The HITL review flow (§5 of ARCHITECTURE.md) requires that only a credit officer can resolve a `ReviewRequest` and resume an interrupted run, and per-org feature flags decide which of the 12 workflows an organisation can even see (ADR-0015). So roles must be first-class claims the API layer can gate on, and tenancy must reach every read path — including Celery workers and the RAG retriever — not just HTTP handlers.

Finally, the judge panel flagged a specific weakness in the winning proposal: it deferred Postgres row-level security as "optional hardening later", leaving application-layer scoping as the *only* isolation mechanism. A single `WHERE org_id = …` clause forgotten in one repository method would be a silent cross-tenant leak. The panel's verdict — echoing the enterprise proposal's defence-in-depth stance — was that this is a cheap-to-fix enterprise credibility gap: for roughly a day of work, isolation becomes a property that requires **two simultaneous bugs** to break. This ADR adopts that graft.

## Decision

- **Identity and tokens: first-party JWT.**
  - `PyJWT` issues short-lived access tokens (~15 min) and rotating refresh tokens, revocable via a `jti` denylist table; signed HS256 with a secret from `pydantic-settings` (fail-fast at boot if unset).
  - Passwords hashed with **argon2id** (`argon2-cffi`). Claims: `sub` (user id), `org_id`, `role`, `jti`, `iat`, `exp`.
  - No SSO, no MFA, no password-reset email flow at launch — the claim shape is deliberately OIDC-compatible so a future external IdP becomes just another token issuer.
- **Roles map directly to product gates.**
  - `analyst` — creates cases, uploads documents, starts runs.
  - `credit_officer` — additionally resolves HITL `ReviewRequest`s (`POST /runs/{id}/hitl/{stage}`) and confirms qualitative-note factor mappings (§9 overlays).
  - `admin` — manages users, per-org feature flags, and retention/deletion.
  - A FastAPI dependency decodes the token and enforces role requirements per route.
- **TenantContext is a mandatory parameter on every repository method.**
  - The auth middleware builds a frozen `TenantContext(org_id, user_id, role, request_id)`; Celery task payloads serialise and rehydrate it, so tenancy survives the API→worker seam.
  - The SQLAlchemy base repository applies the `org_id` predicate at a single choke point — exactly one place where scoping can be wrong, trivially auditable and unit-testable.
  - `org_id` is bound into `structlog` contextvars, so every log line carries its tenant.
- **Postgres row-level security as an independent second layer.**
  - Every tenant-scoped table gets `ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY`, with a policy of the form `USING (org_id = current_setting('app.org_id')::uuid)`.
  - The application connects as a dedicated non-owner role (RLS does not apply to table owners — forcing it and using a separate login role closes that hole).
  - The unit-of-work/session hook issues `SET LOCAL app.org_id = :org` at transaction start — connection-pool-safe because `SET LOCAL` dies with the transaction.
  - Repository scoping and RLS are enforced by different mechanisms in different layers: cross-tenant leakage now requires a repository bug *and* a session-variable bug in the same request.
- **Audit log on every mutation.** An append-only `audit_log` row (actor, org, action, entity ref, payload hash, `request_id`, timestamp) is written **in the same transaction** as the mutation — consistent with the platform's append-only findings ledger, and queryable for the DPDP/bank-audit story.
- **Per-org feature flags gate workflow availability** (`flags.is_on("workflow.esg_analysis", org)` — mechanism in ADR-0015); tenancy and entitlement are separate concerns with separate tables.
- **DPDP-aware PII posture** (§15.6 of ARCHITECTURE.md):
  - account numbers stored masked in extractions (`account_number_masked`);
  - sensitive identifiers redacted from LLM prompts where the task allows;
  - trace payloads redacted at write time in `TraceWriter` (never lazily in the UI);
  - per-tenant retention/deletion exposed as a first-class admin operation.

## Alternatives considered

### Keycloak (or Auth0 / Clerk)
Keycloak is the prescribed-stack default and would supply OIDC, session management, and an admin console. It is also a JVM service with its own database schema, realm configuration, and upgrade cadence — a third stateful thing to babysit on a single laptop, and the most likely container to be broken at demo time for reasons unrelated to the product. Auth0/Clerk avoid the container but violate local-first: the demo then depends on an external SaaS, network, and a free-tier quota. None of the three demonstrates anything a portfolio reviewer cares about — the interesting engineering here is tenant isolation and role-gated HITL, which live in *our* code either way. First-party JWT is ~300 lines, fully offline, and the OIDC-shaped claims keep the IdP door open.

### Schema-per-tenant or database-per-tenant
The strongest isolation on paper, and the right call at hundreds of tenants with contractual data-residency demands. At this tenant count it is pure operational multiplication: every Alembic migration fans out per schema, pgvector/FTS indexes duplicate per tenant, LangGraph checkpoint tables fragment, connection pooling degrades, and `make demo-seed` becomes a loop. Worst, cross-tenant *platform* queries (the super-admin console, global model registry, per-tenant spend roll-ups in §15.7) now require stitching across schemas. Shared tables with `org_id` + RLS give two-layer isolation without forking the operational story.

### Application-layer scoping only (defer RLS)
This was the winning proposal's original position: the repository choke point is auditable, and RLS "complicates local debugging". The judges rejected the deferral, and the reasoning holds: with a single layer, one forgotten predicate in one new repository method — the exact kind of bug a solo developer under deadline writes — is a silent leak with no second net. The claimed debugging cost is real but small (one `SET LOCAL` hook, one migration helper, and the discipline of connecting as the app role), and it is paid once; the failure mode it prevents is existential for a credit platform. Defence-in-depth here costs a day and upgrades the enterprise story permanently.

### RLS as the sole enforcement layer (no TenantContext)
The mirror image: let Postgres do everything and keep repositories tenant-blind. Rejected because RLS only covers Postgres — Redis cache keys, blob-storage paths (ADR-0016), and LLM-cache entries still need application-level tenant scoping, so the `TenantContext` plumbing must exist anyway. RLS-only code is also harder to test (behaviour depends on invisible session state) and makes every missing `SET LOCAL` fail closed into confusing empty result sets rather than an explicit `TypeError` from a missing parameter. Explicit context plus RLS is strictly more debuggable than either alone.

## Consequences

**Positive.**

- Cross-tenant isolation is a two-bug event, enforceable and demonstrable: the Phase 3 acceptance test seeds two orgs and proves the leak test fails with either layer disabled.
- The repository choke point makes tenancy greppable and reviewable in one file.
- Roles gate the HITL portal natively — an auth decision becomes a product feature.
- Zero new containers; auth works fully offline on demo day.
- The audit log plus write-time trace redaction gives an honest DPDP/bank-audit narrative for the Indian buyer.

**Negative / accepted trade-offs.**

- We own password lifecycle, token rotation, and lockout logic — deliberately minimal (no SSO/MFA/SCIM at launch), which a real bank procurement would eventually reject; the OIDC-compatible claim shape is the mitigation, not a solved problem.
- RLS imposes standing discipline: every new tenant-scoped table needs an `org_id` column, a policy, and membership in the policy-coverage test — forgettable without CI enforcement (obligation below).
- Debug sessions that connect as the table owner silently bypass RLS; the developer runbook must mandate the app role.
- HS256 with a shared secret is fine for one issuer/one audience but must become RS256 the moment tokens are consumed by a second service.
- RLS covers only Postgres: Redis keys, LLM-cache entries, and blob paths (ADR-0016) remain single-layer, application-scoped by `org_id` prefix — an accepted asymmetry, since those stores hold no queryable tenant rows.

**Follow-up obligations.**
- Phase 3: RLS policies on all tenant-scoped tables; `TenantContext` base repository; the two-layer cross-tenant test (must fail both with repository scoping disabled and with RLS disabled) as a phase acceptance criterion.
- Phase 3 (CI): a policy-coverage check asserting every table with `org_id` has `FORCE ROW LEVEL SECURITY` and a policy (query `pg_policies` in a pytest).
- Phase 4: JWT access/refresh issuance, argon2 hashing, RBAC route dependencies, audit-log write path in the unit of work; admin retention/deletion operation.
- Phase 8: full RLS/tenancy test suite, including Celery-seam propagation (a task executed without a rehydrated `TenantContext` must fail loudly, not return empty).
- Revisit at first external deployment: RS256 key pairs, MFA for `admin`, and an external IdP behind the existing claim contract.

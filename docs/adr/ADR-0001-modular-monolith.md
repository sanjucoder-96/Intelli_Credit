# ADR-0001: Modular monolith over microservices

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit AI is a multi-workflow corporate intelligence and credit decision platform: twelve launch workflows (credit appraisal, vendor risk, ESG, underwriting, fraud detection, …) sharing one agentic pipeline, one document-intelligence subsystem, one risk engine, and one report generator. The surface area *sounds* like a distributed system — twelve agents, a workflow compiler, ML training and serving, OCR, hybrid RAG, SSE streaming — and the prescribed stack for this class of platform routinely reaches for service decomposition, a dedicated vector database, an external identity provider, and a tracking server per subsystem.

The governing constraints point the other way. One developer must build, debug, and demo the entire platform on a Windows 11 laptop via `docker compose up`; the judged artefact is a 10-minute end-to-end demo, not a load test. There is no scale pressure: a case is 20–60 documents, and even 100 demo cases stay far below any threshold where network-partitioned components pay for themselves. Meanwhile the product's core promise — bank-grade explainability, where a chunk, its document, its `Evidence` rows, its `Finding`s, and its tenant scope stay transactionally consistent — is *easier* to keep inside one process and one database than across service boundaries with eventual consistency.

The real risk is therefore not "monolith won't scale"; it is "monolith decays into a ball of mud" — twelve workflows' worth of code with no enforced boundaries, un-refactorable within weeks. The decision below buys monolith operability while making the internal boundaries as hard as service boundaries, minus the network.

## Decision

Build a **modular monolith**: one backend deployable, mechanically enforced internal layering, exactly two stateful services.

- **One backend image, three deployments.** A single `intellicredit-backend` image runs as `api` (FastAPI + uvicorn: REST, SSE relay, auth, in-process ML inference), `worker` (Celery, threads/solo pool — Windows-safe: ingestion, OCR, LangGraph agent runs, ML jobs, report rendering), and `beat` (Celery beat: portfolio monitoring, health re-scans, news sweeps). One Dockerfile, one dependency set, one CI build; the three roles differ only by command.
- **Exactly two stateful services:** PostgreSQL 17 (`pgvector/pgvector:pg17`) as the everything-store (system of record, vectors, FTS, LangGraph checkpoints, evidence ledger, model registry, feature flags — [ADR-0002](ADR-0002-postgres-as-the-everything-store.md)) and Redis 7 (Celery broker/result backend, LLM cache, rate limiter, SSE pub/sub). Plus one Next.js 15 frontend container. `flower`/`prometheus`/`grafana` ride behind an optional `--profile ops`.
- **Clean Architecture layering** inside `backend/app/`: `domain` (pure entities and value objects, no I/O) ← `application` (use cases + `typing.Protocol` ports) ← `infrastructure` / `agents` / `workflows` / `api` / `tasks` (adapters, runtimes, delivery). Celery tasks are thin shells: deserialize → call the same use case the API calls.
- **The layering is mechanically enforced, not aspirational:** `import-linter` contracts encode the dependency rule and fail CI on violation; `mypy --strict` runs on the whole backend. A boundary that a linter cannot see does not exist.
- **Dependency injection = FastAPI `Depends` + one composition root** (`api/providers.py`) that constructs every adapter and use case explicitly. Celery tasks and the CLI import the same providers. No DI framework.
- **Repository pattern with mandatory tenancy:** every repository method takes a `TenantContext` and applies `org_id` scoping at that single choke point; Postgres row-level security is layered underneath as an independent second guard ([ADR-0013](ADR-0013-auth-and-tenancy.md)), so cross-tenant leakage requires two simultaneous bugs.

## Alternatives considered

### Microservices (per-agent or per-subsystem services)

The shape the domain superficially suggests: an ingestion service, an agent-orchestration service, an ML-serving service, a report service. Rejected on operating cost and failure modes, not fashion. Each service boundary adds a network hop that must be authenticated, retried, versioned, traced, and mocked in tests; the explainability invariant — evidence rows transactionally consistent with the chunks and findings they support — degrades into sagas or outbox choreography across databases. For a solo developer the cost is concrete: every debug session spans N containers' logs, every schema change is a coordinated deploy, and a laptop demo now depends on service discovery working under Docker Desktop on Windows. There is no compensating benefit: nothing here has divergent scaling needs (the worker *is* the hot path, and it scales by adding worker replicas of the same image), and there is no second team to give a service to. Microservices solve an organisational problem this project does not have.

### DDD bounded-context packaging with per-workload containers

The enterprise-lens proposal: seven bounded contexts with per-context imperative ORM mapping, a transactional outbox with a relay container, split `worker-ingest`/`worker-agents`/`worker-ml` deployments, MinIO, and an otel-lgtm container — roughly eleven services. Its best ideas are kept (immutable versioned workflow definitions, append-only findings, import-linter enforcement, RLS defence-in-depth); the packaging is rejected. Dual-model persistence (domain objects + imperative mappers) roughly doubles the code per aggregate across ~15 aggregates for isolation the repository pattern already provides; the outbox-and-relay machinery solves a dual-write problem that does not exist when events, state, and the ledger commit in one Postgres transaction; and eleven containers thrash a Windows 11 laptop during the exact demo being judged. Queue *names* give agents/ingest/ocr isolation (ML jobs ride the low-concurrency `ocr` queue) without separate deployments if contention ever appears.

### DI framework (dependency-injector or punq)

Declarative containers with wiring, providers, and overrides look disciplined, but on a solo project they add a layer of indirection paid on every debug session: "where does this instance come from" stops being a `Ctrl+Click` into `providers.py` and becomes framework archaeology through wiring modules and container hierarchies. dependency-injector additionally couples startup to its wiring lifecycle (a known source of confusing failures under Celery's forking/threading model). FastAPI `Depends` already gives request-scoped injection at the delivery layer; one explicit composition root gives everything else — construction order readable top to bottom, overridable in tests by passing fakes. The framework's payoff (multiple deployment-specific object graphs, config-driven swapping at enterprise scale) never arrives here.

### Plain monolith without mechanical boundary enforcement

The default outcome if layering lives only in a diagram. Rejected because convention does not survive deadline pressure: the first "just import the repo from the agent" shortcut is invisible in review and metastasizes. Twelve workflows and twelve agents multiply any boundary erosion; by the time it hurts, untangling costs weeks. `import-linter` contracts turn the dependency rule into a CI failure at the moment of violation — the cheapest possible enforcement point — and are the difference between a modular monolith and a monolith that was once modular.

## Consequences

**Positive.**

- One `docker compose up` boots the platform; one image to build, scan, and version; one debugger attaches to the whole request path from router to repository.
- Transactions span the evidence ledger, RAG index, and workflow state — a chunk, its `Evidence` rows, and its tenant scope commit or roll back together, which is what makes "walk the judge through the logic" a database query rather than a distributed reconciliation.
- Refactoring across module boundaries is an IDE operation with `mypy --strict` as the safety net; a rename that would be a multi-repo API migration under microservices is a single commit here.
- The demo cannot be killed by inter-service networking, service discovery, or a partially deployed version skew.
- Horizontal growth has an obvious first move that requires no re-architecture: more `worker` replicas, then dedicated Celery queues per workload class.
- Every grafted enterprise-grade control (immutable definition versions, append-only findings, RLS, import-linter) survives intact — the simplification is in deployment topology, not in rigour.

**Negative / accepted.**

- All backend code shares one Python dependency set — a conflicting pin anywhere (ML stack vs. document stack, e.g. numpy/onnxruntime vs. weasyprint transitive constraints) is everyone's problem and must be resolved in one lockfile.
- One image means no independent deploy cadence per subsystem; a worker-only fix still ships the whole image (mitigated: builds are minutes, not release trains).
- Postgres as the everything-store concentrates load: heavy ingestion (embedding and FTS writes) can contend with interactive queries and LangGraph checkpoint writes on laptop-grade hardware; connection-pool sizing and worker concurrency need deliberate tuning (tracked in the risk register).
- The layering adds ceremony a script would not have — ports, adapters, and a composition root are more files than "import the ORM model in the router". This is the price of the twelve-workflow extensibility promise and is paid knowingly.
- True multi-team or multi-region scale would force service extraction later — accepted, because the ports (`StoragePort`, `EmbeddingProvider`, `SourceConnector`, repository interfaces) mark the future seams, and extraction from a layered monolith is a known, bounded operation.

**Follow-up obligations.**
- Write and commit the `import-linter` contracts and `mypy --strict` CI gate in Phase 2 (folder structure), before any feature code lands — enforcement retrofitted late enforces nothing.
- Keep `providers.py` the *only* composition root; any second wiring site is a review-blocking defect.
- Define named Celery queues (`agents`, `ingest`, `ocr` — ML jobs ride the low-concurrency `ocr` queue, per ADR-0004) from the first task, even while one worker consumes them all, so workload isolation is a compose-file change, not a refactor.
- Revisit this ADR if a second backend runtime (e.g. a GPU inference host) or a second team materialises — those are the two forces that legitimately reopen service extraction.

# Architecture Decision Records

Phase-1 decisions for IntelliCredit AI. Each record documents context, the decision, honestly-argued rejected alternatives, and consequences. Several records deliberately **simplify the prescribed stack** (marked ✂) — every deviation is argued from failure modes and operating cost, per the project rule that a simpler architecture with equal capability wins.

| ADR | Decision | Simplifies |
|---|---|---|
| [0001](ADR-0001-modular-monolith.md) | Modular monolith over microservices; import-linter-enforced Clean Architecture; no DI framework | ✂ |
| [0002](ADR-0002-postgres-as-the-everything-store.md) | PostgreSQL 17 + pgvector as the everything-store; hybrid RAG via FTS + HNSW + RRF in one CTE | ✂ no dedicated vector DB / Elasticsearch |
| [0003](ADR-0003-langgraph-only-no-pydantic-ai.md) | LangGraph for orchestration only; direct google-genai + Pydantic | ✂ no Pydantic-AI, no LangChain chains |
| [0004](ADR-0004-celery-task-orchestration.md) | Celery + Redis; ten-stage idempotent ingestion; Celery×LangGraph idempotency rules | |
| [0005](ADR-0005-llm-gateway-and-model-tiering.md) | One LLM gateway: Pro/Flash tiering, cache, rate limiting, cost circuit breaker, record/replay | ✂ no multi-provider routing |
| [0006](ADR-0006-ocr-escalation-chain.md) | RapidOCR primary → Gemini vision escalation, validator-gated; citation-granularity policy | ✂ no PaddleOCR framework / Document AI |
| [0007](ADR-0007-embeddings-local-first.md) | fastembed local ONNX default; Gemini embeddings as config swap | |
| [0008](ADR-0008-workflow-as-data.md) | Workflows as YAML → immutable versions → one GraphCompiler; nine registries; server-driven dashboards | |
| [0009](ADR-0009-deterministic-scorecard-and-policy-grid.md) | Deterministic scorecard + policy grid; the LLM never does arithmetic | |
| [0010](ADR-0010-ml-datasets-and-synthetic-separation.md) | Real public datasets for training; SDV synthetic for demos only; structural separation | ✂ no MLflow |
| [0011](ADR-0011-report-ir-and-renderers.md) | Report IR → Jinja2 → HTML/PDF/DOCX/PPTX/XLSX; citation-enforcing renderer | |
| [0012](ADR-0012-first-party-tracing.md) | The evidence ledger IS the trace | ✂ no LangSmith/Langfuse/Phoenix |
| [0013](ADR-0013-auth-and-tenancy.md) | First-party JWT + TenantContext + Postgres RLS second layer | ✂ no Keycloak/Auth0 |
| [0014](ADR-0014-frontend-stack.md) | Next.js 15, TanStack Query + generated client, SSE, single ECharts library | ✂ no WebSockets/GraphQL |
| [0015](ADR-0015-feature-flags.md) | Postgres-backed feature flags | ✂ no Unleash/LaunchDarkly |
| [0016](ADR-0016-blob-storage-port.md) | StoragePort with local-FS backend; MinIO/S3 deferred | ✂ no MinIO container |

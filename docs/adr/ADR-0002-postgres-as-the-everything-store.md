# ADR-0002: PostgreSQL 17 + pgvector as the everything-store

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

IntelliCredit needs six kinds of state: a relational system of record (cases, companies, documents, findings, evidence, tenants), a vector index for semantic retrieval, a keyword full-text index, a durable checkpoint store for LangGraph runs, an append-only agent trace + evidence ledger, and small operational tables (ML model registry, feature flags). The prescribed stack for this class of platform reaches for a dedicated engine per concern — a vector DB, a search cluster, a tracking server. Every one of those is a new stateful container on a solo developer's Windows 11 laptop running `docker compose up`: its own backup, its own migration story, its own memory footprint, its own failure modes during a live demo.

The scale numbers do not justify any of them. A credit case is 20–60 documents ≈ 4–8k chunks; even 100 fully-loaded demo cases stay under one million chunks platform-wide — orders of magnitude below the corpus sizes where dedicated vector engines earn their operational cost. pgvector HNSW is comfortably sub-50 ms at this scale.

The decisive force, though, is explainability, not scale. The platform's product invariant is that every claim traces to evidence: a chunk row, its parent document, its `Evidence` rows, and its tenant scope must be written (and deleted) atomically. Two stores means dual writes, sync jobs, and delete cascades across a network boundary — and the characteristic failure mode is a citation that resolves to nothing. For a platform whose pitch is "walk the judge through the logic," a dangling citation is the worst possible bug class, and it is structurally impossible when the vector lives in the same row as the text it indexes.

Finally, finance retrieval is identifier-heavy: analysts query exact GSTINs, CINs, IFSC codes, invoice numbers, and cheque references. Pure vector similarity misses exact identifiers routinely, so a keyword arm is non-negotiable — which means the FTS engine choice is coupled to the vector store choice.

## Decision

One PostgreSQL 17 instance (image `pgvector/pgvector:pg17`, pinned to pgvector >= 0.8 for iterative index scans) is the everything-store:

- **System of record** — all domain entities via SQLAlchemy 2.0 async repositories; Alembic migrations from the first table; Postgres row-level security as the second tenancy-isolation layer (ADR-0013).
- **Vector store** — pgvector with an HNSW index, cosine distance. The embedding (`bge-small-en-v1.5`, 384-dim via fastembed, per ADR-0007), the chunk text, its metadata, and its `tsvector` live in **one row**, so index writes are transactional with extraction.
- **FTS engine** — a `tsvector` generated column with a GIN index; queries via `websearch_to_tsquery`, ranked with `ts_rank_cd`.
- **Hybrid retrieval** — one SQL CTE runs both arms (FTS and HNSW cosine, top-50 each) and fuses them with Reciprocal Rank Fusion (k = 60). Filters (case_id, doc_types, fiscal period, tenant) are pushed into both arms, so tenancy scoping is identical for keyword and semantic hits. One caveat: pgvector applies predicates after the HNSW scan, so a selective `case_id` filter can starve the vector arm — we require pgvector >= 0.8 and enable iterative index scans (`hnsw.iterative_scan`, or a raised `ef_search`) to keep filtered top-k complete. For case-scoped queries the planner may legitimately choose an exact scan over the ~8k chunks of a case instead of the index, which is fine at this scale.
- **LangGraph checkpoint store** — `langgraph-checkpoint-postgres` with `thread_id = run_id`; runs survive worker restarts and resume from `interrupt()` without a separate durability service.
- **Agent trace + evidence ledger** — `AgentStep`, `Evidence`, `llm_calls`, and append-only `findings` tables; observability, explainability UI, and cost dashboards are all SQL over the same rows (ADR-0012).
- **ML model registry** — a `model_registry` table (semver, metrics JSONB, lineage hashes, stage) with artifacts on a volume (ADR-0010).
- **Feature flags** — a `feature_flags` table, Redis-cached (ADR-0015).

Result: one backup (`pg_dump` of one database captures records, vectors, FTS, checkpoints, ledger, registry, and flags in a consistent snapshot), one migration story, one connection string, zero sync pipelines.

## Alternatives considered

### Qdrant / Weaviate / Milvus (self-hosted dedicated vector DB)

Rejected. Each adds a second consistency domain: chunk text in Postgres, vectors elsewhere, kept aligned by application-level dual writes with no cross-store transaction. The failure modes are ugly and silent — a crashed worker between the two writes leaves a vector pointing at a deleted chunk, or a chunk that is unretrievable; correction cascades (§15.4 of ARCHITECTURE.md) would need distributed cleanup. Operationally it is a second backup, a second upgrade path, and a second container competing for laptop RAM. The payoff — better recall/latency at tens of millions of vectors, quantization, horizontal sharding — is worth exactly nothing under one million chunks. This is a deliberate deviation from the prescribed stack, argued from failure modes: at our scale the dedicated engine adds risk and removes nothing.

### Pinecone (managed vector service)

Rejected outright. A cloud dependency breaks the local-first constraint — `docker compose up` must produce a fully working system, and the demo must survive an offline venue. It also ships chunk text from bank statements and GST returns (PAN fragments, account narrations) to a third-party US-hosted service, which is indefensible in the platform's DPDP-aware posture for Indian bank/NBFC buyers, and it introduces per-query cost and rate limits on the hottest agent path.

### Elasticsearch / OpenSearch (dedicated FTS, or FTS + kNN hybrid)

Rejected. A JVM cluster wants 1–2 GB of heap before it does anything useful — on a laptop already running Postgres, Redis, three backend containers, and a browser, that is the demo's memory budget. It duplicates capability Postgres already has: at <1M chunks, Postgres FTS with `ts_rank_cd` is equivalent for keyword recall on statutory-form vocabulary and exact identifiers, and ES's analyzer sophistication buys nothing for GSTIN/CIN token matching. It also reintroduces the dual-store consistency problem for the keyword arm, and RRF fusion across two engines becomes application code instead of one CTE.

### Polyglot persistence for the remaining concerns (Redis checkpoints, MLflow registry, Unleash flags)

Rejected as a pattern; the per-tool arguments live in ADR-0010 and ADR-0015. The general failure mode is the same each time: checkpoints in Redis are lost on eviction/restart exactly when resumability matters; a registry or flag service is another stateful box whose contents are now excluded from the one-backup guarantee. Small operational tables are the cheapest possible tenants of a database that is already running.

## Consequences

**Positive.** Transactional consistency between a chunk, its document, its evidence rows, and its tenant scope — dangling citations are structurally impossible. One backup/restore covers everything the platform claims to be able to explain months later. Tenancy (including RLS) applies uniformly to relational, vector, and FTS access because they are the same rows. Cross-cutting queries — per-case LLM spend, evidence-chain replay, model lineage — are joins, not integrations. One fewer container, one fewer failure mode on demo day.

**Negative / accepted.** Everything contends for one laptop-grade instance: bulk embedding writes during ingestion, interactive analyst queries, and LangGraph checkpoint writes share buffers, WAL, and I/O. This is accepted with eyes open — mitigations are separate connection pools for `api` and `worker` sized against `max_connections`, deliberate Celery worker-concurrency limits on the `ingest`/`ocr` queues, and HNSW build tuning (`m`, `ef_construction`, `maintenance_work_mem`) so index maintenance does not stall interactive traffic. Checkpoint and trace tables are high-churn/append-only and will bloat without discipline. And at true production scale — multi-tenant SaaS with tens of millions of chunks — retrieval would eventually graduate to a dedicated engine; we are explicitly not building for that today.

**Follow-up obligations.**

- Commit pool sizes and worker concurrency defaults in `docker-compose.yml` + `pydantic-settings`, and load-test the ingest-while-querying path when the ingestion pipeline lands (Phase: document intelligence build).
- Ship a beat-scheduled retention job for LangGraph checkpoints and gzipped trace payloads (completed runs pruned to the durable `AgentStep`/`Evidence` rows) before portfolio monitoring turns beat on.
- Enable `pg_stat_statements` and a Grafana panel behind `--profile ops` so contention is observable, not anecdotal.
- Keep the graduation path real: all retrieval flows through the `SemanticMemory` protocol / `HybridRetriever` port — a dedicated vector engine must be adoptable as a new adapter with zero agent-code changes. Re-validate this seam whenever the retriever interface changes.
- The embedding-provider swap (ADR-0007) requires a re-embed job that rewrites the vector column and rebuilds the HNSW index; dimension changes are an Alembic migration, planned, not ad hoc.

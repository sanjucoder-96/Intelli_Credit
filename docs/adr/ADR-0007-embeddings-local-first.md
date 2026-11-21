# ADR-0007: Embeddings: fastembed local ONNX default; Gemini embeddings as config swap

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

Every ingested document ends its pipeline in the `embed_index` stage: chunks are embedded and written — text, metadata, `tsvector`, and vector in one Postgres row — so that the hybrid retriever (pgvector HNSW fused with Postgres FTS via RRF, [ADR-0002](ADR-0002-postgres-as-the-everything-store.md)) can serve every agent query, the case AI chat, and CAM generation. Embedding therefore sits on the **hottest path in the system**: it runs for every page of every document of every case, it runs in bulk during `make demo-seed`, and a stalled `embed_index` stage means a case that never becomes searchable. The corpus is small by vector-DB standards — a case is ~20–60 documents ≈ 4–8k chunks; even 100 demo cases stay under one million chunks — and retrieval quality does not rest on the embedder alone: exact identifiers (GSTIN, CIN, IFSC, invoice numbers) that dominate finance queries are carried by the FTS arm, with RRF fusion, so the vector arm's job is semantic recall over prose, not precision lookup.

The operating constraints are the platform invariants: one developer, Windows 11 laptop, `docker compose up`, and a live demo that must never die from API luck. The Document Intelligence specialist originally proposed `gemini-embedding-001` as the default (MTEB-leading, zero serving infrastructure since the stack already holds a Gemini key) with fastembed as an offline fallback. The judge panel inverted that: embedding is the one LLM-adjacent operation that runs unconditionally on every ingest, and putting a metered, rate-limited network API on that path couples ingestion throughput — and demo-day reliability — to quota. The Gemini API dependency is confined to where it earns its cost: generation, vision OCR escalation, and synthesis ([ADR-0005](ADR-0005-llm-gateway-and-model-tiering.md)). ARCHITECTURE.md §8 reflects the panel's position; this ADR records the full reasoning.

A second force is dimensionality economics. The pgvector HNSW index lives on a laptop-grade Postgres instance that is simultaneously the system of record, checkpoint store, and evidence ledger. Index size and build time scale linearly with dimension; at this corpus size, larger vectors buy near-zero retrieval improvement while making every index write and every ANN probe more expensive on shared hardware.

## Decision

- **Default embedder: `fastembed` running `BAAI/bge-small-en-v1.5` — 384 dimensions, ONNX Runtime, CPU, normalised vectors, cosine distance.** Embeddings are computed inside the Celery worker during the `embed_index` ingestion stage. No GPU, no serving process, no network call: ingestion is free, deterministic (same text → same vector, byte-stable across runs), and fully offline.
- **Model weights are baked into the worker image at build time** (fastembed's model cache pre-populated in the Dockerfile), so the first ingestion after `docker compose up` never performs a network download.
- **The chunk table carries `embedding vector(384)`** with an HNSW index (`vector_cosine_ops`); chunk text, metadata, `tsvector`, and the vector remain one transactional row per [ADR-0002](ADR-0002-postgres-as-the-everything-store.md).
- **An `EmbeddingProvider` port** (`typing.Protocol` in `application/`) is the only way any code obtains a vector: `embed_documents(texts) -> list[Vector]`, `embed_query(text) -> Vector`, plus `model_id` and `dim` properties. `FastembedProvider` is the default adapter; **`GeminiEmbeddingProvider` (`gemini-embedding-001`, Matryoshka-truncated to 768 dimensions, re-normalised after truncation)** ships as the alternative, selected purely by `pydantic-settings` configuration.
- **Active provider, model id, and dimension are recorded in configuration and asserted at boot:** startup fails fast if the configured dimension does not match the database column, so a mis-swap can never silently write incompatible vectors.
- **Swapping providers is an explicit migration, not a toggle:** an Alembic migration changes the vector column dimension, and a `reembed_corpus` Celery task re-embeds every chunk (idempotent, checkpointed per document like every ingestion stage) before the HNSW index is rebuilt. Query-time and index-time embeddings always come from the same configured provider.
- **The tripwire for swapping is measured, not felt:** the eval harness's retrieval metrics (hit-rate, MRR on the golden corpus, ARCHITECTURE.md §14) decide whether `bge-small` retrieval quality is insufficient; only a measured gap justifies paying the re-embed and the permanent API coupling.

## Alternatives considered

### gemini-embedding-001 as the default (specialist's original proposal)

The strongest rejected option, and the one the Document Intelligence spec argued for: MTEB-leading quality, no serving infrastructure, and the API key already exists for generation. Rejected by the judge panel because it places a metered network dependency on the hottest path in the system. Every quota exhaustion, rate-limit burst (bulk demo seeding is exactly a burst), regional API incident, or offline demo environment becomes an ingestion outage — the failure mode lands on the stage that must never fail. The quality argument is also weaker than it looks here: retrieval is hybrid, the FTS arm carries the identifier-heavy queries that dominate finance workloads, and RRF fusion plus the feature-flagged Flash reranker recover most of the headroom a stronger embedder would buy at 4–8k chunks per case. Gemini embeddings remain fully available behind the port for the day measurements say otherwise.

### Self-hosted large embedding models (BGE-M3, e5-large, bge-large, instructor-xl)

Better raw quality than `bge-small`, but they impose a real serving burden: hundreds of MB to multiple GB of weights, materially slower CPU inference (or a GPU the target machine does not dependably have under Docker on Windows 11), larger worker images, and longer ingestion times — all carried by one developer on a laptop, for retrieval gains that are marginal at this corpus size and further diluted by hybrid fusion. If quality headroom is ever needed, the honest comparison is Gemini embeddings via the existing port (zero serving cost) rather than heavier local serving.

### gemini-embedding-001 at native 3072 dimensions

Even in the Gemini-swap scenario, native dimensionality is rejected. 3072-dim vectors quadruple storage and HNSW index cost versus 768, slow index builds and probes on shared laptop Postgres, and the Matryoshka training objective means truncation to 768 (with re-normalisation) loses almost nothing at this scale. The specialist and the panel agreed on this point: if the swap ever happens, it happens at 768.

### Dedicated embedding/inference service (TEI, Infinity, or a vector DB with built-in embedding)

A separate embedding server is a new container, a new health check, and a new failure domain to babysit — rejected under the "every component must earn a new box" rule for the same reasons a dedicated vector DB was rejected in [ADR-0002](ADR-0002-postgres-as-the-everything-store.md). fastembed in-process in the worker delivers the same models with zero additional topology.

## Consequences

**Positive.**

- Ingestion has no API cost, no quota ceiling, and no network failure mode; `make demo-seed` and demo-day ingestion are deterministic and offline-capable, which composes with the replay gateway into a fully offline demo story.
- 384-dim vectors keep the HNSW index small and fast on laptop-grade Postgres, and keep index writes cheap where they contend with LangGraph checkpoint and evidence-ledger writes on the shared instance.
- Deterministic embeddings make retrieval tests reproducible in CI without API keys or recorded fixtures — the same seam the replay gateway provides for generation, achieved for free here.
- The `EmbeddingProvider` port means the default is a decision, not a lock-in; the swap is configuration plus a rehearsed migration, never a refactor.

**Negative / accepted trade-offs.**

- `bge-small-en-v1.5` is measurably below frontier embedding quality. We accept a semantic-recall gap that hybrid fusion mitigates but does not erase, and we accept that the feature-flagged Flash reranker may need enabling for the highest-stakes queries (CAM generation) sooner than a stronger embedder would require.
- The model is English-only. That is acceptable for the initial corpus — Indian statutory and banking documents (GSTR returns, ITRs, CIBIL Commercial reports, sanction letters) are overwhelmingly English — but vernacular documents (Hindi board minutes, regional-language legal notices) would degrade the vector arm; the scope question is tracked in the risk register alongside the equivalent OCR language question.
- Embedding dimension is a **migration-time commitment per provider**: switching means an Alembic migration plus a full corpus re-embed and HNSW rebuild — deliberate friction, cheap at demo scale (< 1M chunks), non-trivial for a large production tenant.
- Mixed-provider corpora are structurally impossible (boot assertion, single configured provider), which is the safe choice but rules out per-tenant embedder experiments without separate deployments.

**Follow-up obligations.**

- Implement the `EmbeddingProvider` port, both adapters, the boot-time dimension assertion, and image-baked model weights in Phase 4 (ingestion pipeline), alongside the `embed_index` stage.
- Implement the checkpointed `reembed_corpus` Celery task and its Alembic migration recipe in the same phase — the swap path must be real, not aspirational.
- Add retrieval hit-rate/MRR over the golden corpus to the eval harness (Phase 6/8, with the fabricated scan corpus) and record a fastembed-vs-Gemini comparison there; that measurement — not taste — is the standing trigger for revisiting this default.
- Revisit this ADR if vernacular documents enter scope: the decision then is multilingual `bge-m3`-class local serving versus the Gemini swap, and the eval harness comparison above is the input to it.

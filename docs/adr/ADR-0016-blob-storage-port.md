# ADR-0016: Blob storage: StoragePort with local-filesystem backend; MinIO/S3 deferred

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

The platform stores five families of binary artifacts: uploaded originals (PDFs, DOCX, XLSX, emails, ZIP bundles — a credit case carries 20–60 documents), 300-DPI page images rendered for OCR and for the document viewer's citation deep-links, **web-evidence snapshots** (every research citation archives the fetched content at claim time, so link rot or page edits cannot break "walk the judge through it" months later — ARCHITECTURE.md §6), rendered report artifacts (HTML/PDF/DOCX/PPTX/XLSX), and ML model artifacts referenced by the Postgres `model_registry`. Both the API container and the Celery workers need the same bytes: the API accepts uploads and serves the viewer; workers render pages, OCR, snapshot web sources, and write reports.

The constraints are the architecture's third invariant made concrete: one developer, Windows 11, `docker compose up`, and exactly two stateful services (PostgreSQL, Redis) — every new box must earn its place. At the same time, bank-grade explainability imposes permanence requirements ordinary file handling ignores: a citation recorded in the append-only evidence ledger must resolve to byte-identical content long after the run, and uploaded files are **adversarial inputs** (§15.2) that need one enforced choke point for size/type validation and a future malware scanner.

The panel material conflicted here. The document-intelligence specialist (spec_1) argued MinIO-first: S3 API, SHA-256 content-addressed keys, and presigned URLs so the Next.js viewer streams PDFs without occupying FastAPI workers. The winning simplicity proposal stored page images and model artifacts as bare files on a Docker volume. The judges resolved it both ways at once: drop the MinIO container (one less stateful service on a laptop) **and** graft a thin `StoragePort` abstraction over the volume so the files-on-a-volume choice never leaks paths through the codebase, plus mandatory web-snapshot archiving. ARCHITECTURE.md §14 records that resolution; this ADR is its full argument.

## Decision

- **`StoragePort`** is a `typing.Protocol` defined in `application/` (ports layer); adapters live in `infrastructure/`, per the import-linter-enforced dependency rule (§3).
- **Interface:** `put(stream, *, case_id, ext, content_type) -> BlobRef` (computes SHA-256 incrementally while streaming to storage), `get(ref) -> AsyncIterator[bytes]`, `stream(ref, byte_range)`, `url_for(ref, ttl) -> str`, `exists(case_id, sha256) -> bool`, `delete(ref)` (retention/DPDP operations only — never workflow logic; evidence blobs are immutable).
- **Content-addressed keys:** `cases/{case_id}/{sha256}.{ext}`. Same bytes uploaded twice **within a case** deduplicate to one blob (the same bytes uploaded to two cases store twice — the key is case-scoped); integrity is verifiable by re-hashing; keys are immutable, matching the append-only findings model. Global dedup (`blobs/{sha256}` plus a case–blob association table) is a possible later optimization; it is deferred because it complicates tenant purge — a blob could only be deleted when the last referencing case is purged.
- **`BlobRef`** is a value object (`key`, `sha256`, `size_bytes`, `content_type`). Database rows and LangGraph state (`ArtifactRef`) carry `BlobRef`s exclusively — raw filesystem paths exist only inside the local adapter. No `os.path` outside `infrastructure/`.
- **Everything goes through it:** uploaded originals, page images, web-evidence snapshots (extracted text + fetch metadata, content-hashed), report artifacts, and ML model artifacts (the `model_registry.artifact_path` column stores a storage key, not a path).
- **Single-node backend:** `LocalFilesystemStorage` writing to a **named Docker volume** mounted into `api`, `worker`, and `beat` (a named volume, not a Windows bind mount — WSL2 bind-mount I/O is markedly slower and permission-fragile).
- **Serving:** an authenticated FastAPI route streams blob bytes with **HTTP Range support** (pdf.js fetches pages lazily via range requests), after a `TenantContext` ownership check. `url_for` on the local backend returns this route with a short-TTL token, so viewer code is identical when the S3 adapter's real presigned URLs arrive.
- **Malware-scan hook point:** `put()` runs registered `ScanHook`s before the blob becomes visible. Day one the hooks are the §15.2 guards (size/page caps, magic-byte validation, ZIP depth and cumulative-size limits); ClamAV registers later without touching any call site.
- **S3-compatible adapter deferred:** an `S3Storage` adapter (aioboto3, works against MinIO or AWS S3) is selected by `STORAGE_BACKEND=local|s3` in `pydantic-settings` when the deployment goes multi-host. A shared **contract test suite** runs both adapters against identical protocol tests; the local adapter passes it in Phase 4, and passing it is the acceptance gate for the S3 adapter whenever it is built.

## Alternatives considered

### MinIO container from day one (the document-intelligence specialist's recommendation)

The specialist's case was genuine: presigned URLs let the browser stream large PDFs without tying up FastAPI workers, the S3 API decouples storage from hosts, and content-addressing comes naturally. Those benefits are real — **at multi-host scale**. On a single 16 GB Windows laptop, MinIO is a third stateful service with its own credentials, console, healthcheck, upgrade cadence, and backup story, and it pays for itself only when there are multiple hosts or streaming volumes uvicorn cannot absorb. At demo scale (a handful of concurrent viewers, ≤ 60 documents per case) FastAPI range-streaming is negligible load, and the port makes the eventual swap a config change plus a content-hash-keyed copy job. The judges' ruling stands: this is a **deferral, not a rejection** — the specialist's design becomes the S3 adapter's design when multi-host arrives.

### Bare file paths on a shared volume, no port

The winning proposal's original shape ("page images stored on a volume"). It fails slowly and expensively: path strings leak into database rows, graph state, and templates; every consumer grows OS-specific path handling; there is no content addressing, so duplicate uploads multiply and nothing can verify that cited evidence is unaltered; there is no choke point for scanning or tenancy checks; and the eventual S3 migration becomes a grep-driven rewrite of every call site. The judge panel grafted the port precisely to kill this failure mode while keeping the volume.

### Postgres `bytea` / large objects

Superficially attractive given ADR-0002's everything-store stance — one backup, transactional blobs. But blobs are exactly where the everything-store argument inverts: page images dominate volume (a 100-page scanned statement yields ~100 PNGs), and pushing them through Postgres means WAL amplification, dump/restore bloat, vacuum pressure, and buffer-cache pollution that degrades the pgvector HNSW and FTS workloads the platform actually depends on. Range-streaming PDFs out of `bytea` is also awkward. Postgres is the system of record for *structured and derived* data; bytes live behind the port.

### Real cloud S3 (AWS) from day one

Violates local-first outright: ingestion becomes network-bound (every page image is a round trip), the demo dies with conference Wi-Fi, and a solo developer inherits credentials management and a monthly bill for what a Docker volume does for free. The S3 adapter exists for when deployment genuinely leaves the laptop, not before.

## Consequences

**Positive**

- The stateful-service count stays at exactly two; `docker compose up` stays boring; backup is one `pg_dump` plus one volume copy (Phase 9 scripts).
- Content addressing gives free deduplication within a case, tamper-evidence (citation → SHA-256 → byte-identical bytes months later), and makes the §6 web-snapshot permanence promise mechanically true rather than aspirational.
- One choke point serves upload hardening (§15.2), tenancy enforcement on byte access, and DPDP per-tenant retention/deletion (§15.6) — a tenant purge is `delete()` over a key prefix plus row supersession.
- Tests run against a tmpdir fake of the same protocol; ingestion tests need no external service, keeping CI key-free and fast.

**Negative / accepted**

- The API process serves file bytes. Acceptable at demo scale; it is the first identified bottleneck if concurrent viewer traffic grows, and the S3 adapter's presigned URLs are the designed relief valve — we accept deliberately deferring that work.
- Single-host coupling is real: api, worker, and beat must mount the same volume. That is the accepted shape of the single-node deployment, not an oversight.
- Local `url_for` is not a true presign — it returns an authenticated API route with a TTL token. The semantic difference between backends is confined behind the port, but it exists and the contract tests must cover both.
- Named Docker volumes are opaque to Windows Explorer; inspecting blobs requires `docker cp` or a debug container — a minor developer-experience tax versus a bind mount, paid for the I/O and permission reliability.

**Follow-up obligations**

- **Phase 4:** implement `StoragePort`, `LocalFilesystemStorage`, the scan-hook harness, the range-streaming route, and the adapter contract test suite; wire upload, page-image, snapshot, report, and ML-artifact writes through it.
- **Phase 9:** volume backup/restore scripts alongside the Postgres story; compose resource limits sized with blob growth in mind.
- **When multi-host arrives:** build the S3/MinIO adapter against the existing contract suite; migration is a content-hash-keyed copy job (keys are host-independent), not a re-ingest.
- **First external pilot:** register a real malware scanner (ClamAV) behind the `ScanHook` point; until then the §15.2 structural guards are the accepted level of protection.

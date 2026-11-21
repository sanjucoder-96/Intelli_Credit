# data/synthetic — demo-only synthetic data

This tree holds **synthetic demo data only** — fabricated Indian corporate
entities (statements, GSTR returns, bank statements) used for demos and the
Phase-6/8 scanned-document golden corpus. It is generated honestly (SDV
Gaussian copula fitted to the *real* datasets' ratio correlation structure,
marginals re-scaled to RBI sector aggregates) and is **never used to train
any model** (ADR-0010,
`docs/adr/ADR-0010-ml-datasets-and-synthetic-separation.md`).

## Separation is structural

- Every synthetic row and entity carries `data_origin = SYNTHETIC_DEMO`
  (the `DataOrigin` enum in `backend/app/domain/enums.py`).
- Synthetic entities get a provenance badge in the UI — the product never
  passes fabricated companies off as real ones.
- Training manifests may reference only `data/real/`; lineage hashes in the
  `ml_models` registry make contamination detectable after the fact.
- **CI guard rule: the build fails if any training manifest references
  `SYNTHETIC_DEMO` data.** The guard ships with the first trained model in
  Phase 6.

## What lands here

- Phase 6: SDV-generated demo companies with internally consistent,
  back-solved statement line items; injected fraud-pattern cases for the
  forensic-rules demos.
- Phase 6/8: the render -> print/scan-degrade fabrication pipeline output — the
  OCR golden corpus of realistic scanned Indian documents.
- Phase 9: `make demo-seed` fixtures (demo tenant + three seeded cases).

## Rules

- Generated files are **never committed** (see `.gitignore` — only this README
  and `.gitkeep` are tracked); the generator is reproducible from code.
- Nothing in this tree may appear in `data/real/` or in any training manifest.

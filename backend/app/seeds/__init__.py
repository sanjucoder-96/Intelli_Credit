"""Seed scripts (Phase 3): demo tenant, users per role, empty demo cases.

Everything seeded here is SYNTHETIC_DEMO-labeled where the schema carries a
``data_origin`` column (ADR-0010 separation rule). The full demo dataset
(documents, pre-warmed runs, replay fixtures) is the Phase-9 ``make
demo-seed`` deliverable — these seeds make a fresh database usable for
development and the Phase-3 acceptance tests.

Run: ``python -m app.seeds.demo`` (admin database connection required).
"""

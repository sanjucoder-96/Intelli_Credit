# Workflow definitions

Workflow YAML definitions land in **Phase 5** — `credit_appraisal` first, then
`vendor_risk` (Phase 7) and `esg_analysis` (Phase 8), after which the DSL is
frozen (harvest-the-abstraction rule, [ADR-0008](../../../../docs/adr/ADR-0008-workflow-as-data.md)).

From Phase 5, files here will be loaded and registered at startup by
`app.workflows.registry.WorkflowRegistry.load_builtin` (the mechanism exists
and is tested today), validated fail-fast against `WorkflowDefinition`,
checksummed, and persisted as immutable versions in the `workflows` table.

The Phase-2 test suite exercises the loader/compiler with a fixture definition
under `backend/tests/fixtures/` — deliberately *not* in this directory, which
stays empty until a real workflow ships.

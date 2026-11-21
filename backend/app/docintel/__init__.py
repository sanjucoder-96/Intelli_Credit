"""Document intelligence: type registry, extraction schemas, extractors,
post-validators, and cross-document validators (ARCHITECTURE §7).

Layering note: this package is infrastructure-adjacent domain tooling — pure
parsing/validation logic with no I/O of its own (the pipeline stages in
``app.tasks``/``app.ingestion`` feed it bytes and persist its outputs), so it
sits at the ``app.infrastructure`` layer in the import-linter contract.
"""

from app.docintel.registry import DocTypeSpec, document_type_registry

__all__ = ["DocTypeSpec", "document_type_registry"]

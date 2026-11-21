"""Infrastructure layer: concrete adapters behind application ports.

Phase 2 ships the async SQLAlchemy persistence stack and the Redis client.
Phase 3 adds the StoragePort adapters and the retriever; Phase 4 adds the
OCR service; Phase 5 adds the LLM gateway (ADR-0005).
"""

"""Redis adapters (async client, readiness probe).

Phase 2 ships only the lazy singleton client and ping. Phase 4 adds the rate
limiter and Celery integration points; Phase 5 adds the LLM response cache
(ADR-0005) and SSE pub/sub relay.
"""

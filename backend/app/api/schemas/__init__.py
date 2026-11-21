"""Pydantic request/response schemas for the HTTP surface.

Kept separate from ``app.domain.entities`` so the wire contract can evolve
independently of the domain read models. New routers add sibling modules
here (companies/documents in Phase 3, runs in Phase 4).
"""

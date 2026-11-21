"""Request-scoped tenancy and correlation context.

Every repository method requires a :class:`TenantContext` so ``org_id``
scoping is applied at a single choke point (ADR-0013). Phase 3 adds Postgres
row-level security as the second, independent isolation layer.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from app.domain.enums import Role


@dataclass(frozen=True)
class TenantContext:
    """Immutable identity of the acting user within one organization."""

    org_id: UUID
    user_id: UUID
    role: Role


current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
"""Correlation ID of the in-flight HTTP request (set by RequestContextMiddleware)."""

"""Audit-log writes (ADR-0013: audit rows on every mutation)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import AuditLog


async def record_audit(
    session: AsyncSession,
    *,
    org_id: UUID,
    actor_id: UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Append one audit row in the caller's transaction (same-commit rule)."""
    session.add(
        AuditLog(
            org_id=org_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            ip=ip,
        )
    )

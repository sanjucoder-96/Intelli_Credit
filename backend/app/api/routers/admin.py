"""Admin endpoints (Phase 4): feature-flag management (ADR-0015)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.providers import (
    get_db_session,
    get_flag_service,
    get_tenant_context,
    require_permissions,
)
from app.api.schemas.cases import FlagResponse, FlagSetRequest
from app.core.context import TenantContext
from app.domain.enums import Permission
from app.infrastructure.db.repositories.audit import record_audit
from app.infrastructure.flags import CachedFlagService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/flags",
    response_model=list[FlagResponse],
    dependencies=[Depends(require_permissions(Permission.FLAGS_MANAGE))],
)
async def list_flags(
    flags: Annotated[CachedFlagService, Depends(get_flag_service)],
) -> list[FlagResponse]:
    """All flag rows across scopes (admin-only)."""
    return [FlagResponse.model_validate(row) for row in await flags.dump()]


@router.put(
    "/flags",
    response_model=FlagResponse,
    dependencies=[Depends(require_permissions(Permission.FLAGS_MANAGE))],
)
async def set_flag(
    body: FlagSetRequest,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    flags: Annotated[CachedFlagService, Depends(get_flag_service)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FlagResponse:
    """Upsert a flag (global/org/workflow scope) and bust its cache."""
    flag = await flags.set_flag(
        key=body.key,
        scope=body.scope,
        enabled=body.enabled,
        org_id=body.org_id,
        workflow_key=body.workflow_key,
    )
    await record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="flag.set",
        entity_type="feature_flag",
        entity_id=flag.id,
        payload={"key": body.key, "scope": body.scope.value, "enabled": body.enabled},
    )
    return FlagResponse(
        key=flag.key,
        scope=flag.scope.value,
        org_id=str(flag.org_id) if flag.org_id else None,
        workflow_key=flag.workflow_key,
        enabled=flag.enabled,
        value=flag.value,
    )

"""Redis-cached feature-flag service (ADR-0015, the Phase-4 roadmap item).

Wraps :class:`FeatureFlagRepository` with a 30-second read-through cache;
writes bust the affected keys. Env overrides keep working (they short-circuit
inside the repository, before the cache is consulted — the local-dev escape
hatch stays instant).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.domain.enums import FlagScope
from app.infrastructure.db.models import FeatureFlag
from app.infrastructure.db.repositories.feature_flags import FeatureFlagRepository

_TTL_SECONDS = 30


def _cache_key(key: str, org_id: UUID | None) -> str:
    return f"flags:{key}:{org_id or 'global'}"


class CachedFlagService:
    """FeatureFlagPort adapter: repository + Redis read-through cache."""

    def __init__(self, session: AsyncSession, redis: Any) -> None:
        self._repository = FeatureFlagRepository(session)
        self._session = session
        self._redis = redis

    async def is_on(self, key: str, ctx: TenantContext | None = None) -> bool:
        cache_key = _cache_key(key, ctx.org_id if ctx else None)
        cached = None
        with contextlib.suppress(Exception):
            # Cache failure degrades to a DB read, never an error.
            cached = await self._redis.get(cache_key)
        if cached is not None:
            value = cached.decode() if isinstance(cached, bytes) else str(cached)
            return value == "1"

        result = await self._repository.is_on(key, ctx)
        with contextlib.suppress(Exception):
            await self._redis.set(cache_key, "1" if result else "0", ex=_TTL_SECONDS)
        return result

    async def set_flag(
        self,
        *,
        key: str,
        scope: FlagScope,
        enabled: bool,
        org_id: UUID | None = None,
        workflow_key: str | None = None,
    ) -> FeatureFlag:
        """Upsert a flag row and bust its cache entries (admin API path)."""
        stmt = sa.select(FeatureFlag).where(
            FeatureFlag.key == key,
            FeatureFlag.scope == scope,
            FeatureFlag.org_id == org_id,
            FeatureFlag.workflow_key == workflow_key,
        )
        flag = (await self._session.execute(stmt)).scalars().first()
        if flag is None:
            flag = FeatureFlag(
                key=key, scope=scope, org_id=org_id, workflow_key=workflow_key, enabled=enabled
            )
            self._session.add(flag)
        else:
            flag.enabled = enabled
        await self._session.flush()

        for cache_key in (_cache_key(key, org_id), _cache_key(key, None)):
            with contextlib.suppress(Exception):
                await self._redis.delete(cache_key)
        return flag

    async def dump(self) -> list[dict[str, Any]]:
        """All flag rows (admin listing)."""
        result = await self._session.execute(
            sa.select(FeatureFlag).order_by(FeatureFlag.key, FeatureFlag.scope)
        )
        return [
            {
                "key": flag.key,
                "scope": flag.scope.value,
                "org_id": str(flag.org_id) if flag.org_id else None,
                "workflow_key": flag.workflow_key,
                "enabled": flag.enabled,
                "value": json.loads(json.dumps(flag.value)) if flag.value else None,
            }
            for flag in result.scalars()
        ]

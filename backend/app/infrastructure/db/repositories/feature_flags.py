"""Feature-flag repository implementing
:class:`app.application.ports.FeatureFlagPort` (ADR-0015).

Resolution order (highest wins): ``IC_FLAG_<KEY>`` environment override →
org-scoped row → global row → False. No Redis cache yet — the read-through
cache with invalidation lands in Phase 4; at Phase-2 call volumes a direct
query is fine.
"""

from __future__ import annotations

import os
import re

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.domain.enums import FlagScope
from app.infrastructure.db.models.platform import FeatureFlag

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ENV_SAFE = re.compile(r"[^A-Z0-9]")


def _env_var_name(key: str) -> str:
    """Map a flag key to its env-override variable name.

    ``workflow.esg_analysis`` → ``IC_FLAG_WORKFLOW_ESG_ANALYSIS`` (uppercase,
    every non-alphanumeric character collapsed to ``_``).
    """
    return "IC_FLAG_" + _ENV_SAFE.sub("_", key.upper())


class FeatureFlagRepository:
    """Async SQLAlchemy adapter for feature-flag resolution."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_on(self, key: str, ctx: TenantContext | None = None) -> bool:
        """Resolve a boolean flag: env override > org override > global.

        The environment override wins in both directions — setting the
        variable to a falsy value force-disables a flag that is enabled in
        the database (local-dev escape hatch per ADR-0015).
        """
        env_value = os.environ.get(_env_var_name(key))
        if env_value is not None:
            return env_value.strip().lower() in _TRUTHY

        # The unique constraint is (key, scope, org_id, workflow_key), so a
        # key+scope+org query may legitimately match several rows (different
        # workflow_key values). Deterministic first-row semantics: prefer the
        # scope-wide row (workflow_key IS NULL), newest last. Phase 3 adds
        # CHECK constraints tying scope to which reference columns may be set.
        if ctx is not None:
            org_stmt = (
                sa.select(FeatureFlag.enabled)
                .where(
                    FeatureFlag.key == key,
                    FeatureFlag.scope == FlagScope.ORG,
                    FeatureFlag.org_id == ctx.org_id,
                )
                .order_by(FeatureFlag.workflow_key.is_(None).desc(), FeatureFlag.created_at)
                .limit(1)
            )
            org_enabled: bool | None = (await self._session.execute(org_stmt)).scalar_one_or_none()
            if org_enabled is not None:
                return org_enabled

        global_stmt = (
            sa.select(FeatureFlag.enabled)
            .where(
                FeatureFlag.key == key,
                FeatureFlag.scope == FlagScope.GLOBAL,
            )
            .order_by(FeatureFlag.workflow_key.is_(None).desc(), FeatureFlag.created_at)
            .limit(1)
        )
        result = await self._session.execute(global_stmt)
        global_enabled: bool | None = result.scalar_one_or_none()
        return bool(global_enabled)

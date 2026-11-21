"""Application-role bootstrap for row-level security (ADR-0013).

RLS only bites for non-superuser roles, so tenant traffic runs as
``intellicredit_app`` — created idempotently here (used by the seed script,
the integration fixtures, and documented for ops). Policies themselves live
in migration 0002; this module owns role + grants only.

Dev credentials are deliberately simple and local-only (the compose network);
production deployments manage this role through their own secret
infrastructure and pass its DSN via ``IC_DATABASE__RLS_URL``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

APP_ROLE = "intellicredit_app"
DEV_APP_ROLE_PASSWORD = "intellicredit_app"  # noqa: S105 - dev/compose only, documented above

_ENSURE_ROLE_SQL = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{DEV_APP_ROLE_PASSWORD}';
    END IF;
END
$$;
"""

# Idempotent grants: the app role reads/writes rows (RLS decides WHICH rows)
# but never owns tables, so it cannot ALTER/DISABLE the policies that bind it.
_GRANTS_SQL = (
    f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}",
)


async def ensure_app_role(admin_engine: AsyncEngine) -> None:
    """Create the RLS application role (if missing) and refresh its grants.

    Requires an admin/owner connection. Safe to run repeatedly; new tables
    are covered by the ``ALTER DEFAULT PRIVILEGES`` statements, and each
    migration run may call this again to re-sync grants.
    """
    async with admin_engine.begin() as conn:
        await conn.execute(sa.text(_ENSURE_ROLE_SQL))
        for statement in _GRANTS_SQL:
            await conn.execute(sa.text(statement))


def rls_dsn_from_admin(admin_dsn: str) -> str:
    """Derive the dev app-role DSN from the admin DSN (same host/db)."""
    url = sa.engine.make_url(admin_dsn)
    return url.set(username=APP_ROLE, password=DEV_APP_ROLE_PASSWORD).render_as_string(
        hide_password=False
    )

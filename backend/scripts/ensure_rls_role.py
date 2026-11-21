"""Ensure the row-level-security application role exists (idempotent).

Run once after ``alembic upgrade head`` so ``docker compose up`` yields a
database that tenant sessions can use immediately (the RLS role is a runtime
bootstrap, not an Alembic migration — it grants DML without table ownership so
the app can never ALTER a policy; ADR-0013).

    python -m scripts.ensure_rls_role
"""

from __future__ import annotations

import asyncio
import selectors
import sys

from app.infrastructure.db.engine import get_engine
from app.infrastructure.db.rls import ensure_app_role


async def _main() -> None:
    engine = get_engine()
    await ensure_app_role(engine)
    await engine.dispose()
    print("RLS application role ensured.")


def main() -> None:
    loop = (
        asyncio.SelectorEventLoop(selectors.SelectSelector())
        if sys.platform == "win32"
        else asyncio.new_event_loop()
    )
    try:
        loop.run_until_complete(_main())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

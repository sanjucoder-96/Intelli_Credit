"""LangGraph durable checkpointer over the project's PostgreSQL (S3.2).

Durable execution needs a checkpointer whose state survives a process death.
LangGraph's ``AsyncPostgresSaver`` stores checkpoints in Postgres tables, so
the durable state lives in the database — the saver object itself is just a
connection. That means one saver per run/task is correct: a resumed run reads
its checkpoints straight from Postgres regardless of which worker opens the
saver (the R19 event-loop rule — a fresh connection per ``asyncio.run`` —
applies here exactly as it does to Redis).

``psycopg`` (not asyncpg) backs the saver, so the DSN is the admin URL with
the ``+asyncpg`` driver tag stripped. ``setup()`` creates the checkpoint
tables and is idempotent; it runs once per process behind a lock.

Tests that want a checkpointer without Postgres use LangGraph's ``MemorySaver``
directly — durability is exactly what distinguishes the two.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("app.workflows.checkpointer")

_setup_done = False
_setup_lock = asyncio.Lock()


def psycopg_dsn() -> str:
    """The admin Postgres DSN as a libpq/psycopg connection string."""
    url = get_settings().database.url
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


async def _ensure_setup(saver: AsyncPostgresSaver) -> None:
    """Create the checkpoint tables once per process (idempotent DDL)."""
    global _setup_done
    if _setup_done:
        return
    async with _setup_lock:
        if _setup_done:
            return
        await saver.setup()
        _setup_done = True
        logger.info("checkpointer_setup_complete")


@asynccontextmanager
async def build_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """Open a Postgres-backed checkpointer for the duration of one run.

    Durable state persists in Postgres, so resume works across a fresh saver
    in a fresh process. Raises if Postgres/psycopg is unavailable — durable
    execution is not silently downgraded (use ``MemorySaver`` explicitly for
    the non-durable path).
    """
    async with AsyncPostgresSaver.from_conn_string(psycopg_dsn()) as saver:
        await _ensure_setup(saver)
        yield saver

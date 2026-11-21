"""Stage framework: checkpointed, idempotent, observable (ARCHITECTURE §15.3).

Execution contract per (document, stage):

1. read the ``document_stages`` checkpoint — ``DONE`` means no-op (this is
   what makes Celery's at-least-once delivery safe);
2. mark RUNNING (attempt += 1), publish a progress event;
3. run the handler inside a transaction scope it controls;
4. mark DONE/FAILED (+ machine-readable error), publish again;
5. report whether the chain should advance to the next stage.

Handlers receive a :class:`StageContext` built by :func:`build_context` —
the single seam tests and the Phase-5 gateway override (dependency factories,
never module-level singletons in handlers).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.ports import EmbeddingProviderPort, StoragePort
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.domain.enums import DocumentStatus, IngestStage, StageStatus
from app.infrastructure.db.engine import create_engine_and_factory
from app.infrastructure.db.models import Document, DocumentStage
from app.infrastructure.embeddings import get_default_embedder as _get_default_embedder
from app.infrastructure.gemini import GeminiClient
from app.infrastructure.redis.client import create_redis
from app.infrastructure.storage.local import LocalFilesystemStorage
from app.ingestion.events import publish_event, stage_event

logger = get_logger("app.ingestion.stages")

STAGE_ORDER: tuple[IngestStage, ...] = (
    IngestStage.DETECT_TYPE,
    IngestStage.EXTRACT_TEXT_LAYER,
    IngestStage.OCR_PAGES,
    IngestStage.LAYOUT_TABLES,
    IngestStage.CLASSIFY,
    IngestStage.EXTRACT_DOMAIN,
    IngestStage.CHUNK,
    IngestStage.EMBED_INDEX,
    IngestStage.CROSS_VALIDATE,
)


def get_embedder() -> EmbeddingProviderPort:
    """Process-wide embedding provider (ADR-0007); tests monkeypatch this.

    Thin alias for the shared provider in ``app.infrastructure.embeddings`` so
    the ingestion pipeline and the agent tools use one lazily-loaded model.
    """
    return _get_default_embedder()


@dataclass
class StageContext:
    """Everything a stage handler may touch."""

    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    storage: StoragePort
    gemini: GeminiClient
    embedder: EmbeddingProviderPort
    redis: Any


@dataclass
class StageResult:
    """Handler outcome: whether to advance, plus optional status/error."""

    advance: bool = True
    document_status: DocumentStatus | None = None
    error: dict[str, Any] | None = None
    extra_documents: list[UUID] | None = None  # ZIP children to fan out


StageHandler = Callable[[StageContext, Document, bytes], Awaitable[StageResult]]


def build_context(engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]) -> StageContext:
    """Assemble handler dependencies from settings (the override seam).

    The Redis client is created fresh per invocation: Celery task bodies run
    on a new event loop each time, and an asyncio client's locks bind to the
    creating loop (the R19 seam — a singleton here breaks the second task).
    """
    settings = get_settings()
    return StageContext(
        settings=settings,
        session_factory=factory,
        storage=LocalFilesystemStorage(settings.storage.root),
        gemini=GeminiClient(settings.gemini),
        embedder=get_embedder(),
        redis=create_redis(),
    )


async def run_stage(
    document_id: UUID,
    stage: IngestStage,
    *,
    context: StageContext | None = None,
) -> StageResult:
    """Execute one stage with checkpoint idempotency; returns its result.

    With no ``context`` supplied (the Celery path), a fresh engine is built
    and disposed per invocation — correct-first; worker-lifetime pooling is a
    Phase-5 optimization noted in the report.
    """
    from app.ingestion import handlers  # local import: avoids cycle at module load

    owns_engine = context is None
    engine: AsyncEngine | None = None
    if context is None:
        engine, factory = create_engine_and_factory(get_settings().database)
        context = build_context(engine, factory)
    try:
        async with context.session_factory() as session:
            document = await session.get(Document, document_id)
            if document is None:
                logger.warning("stage_document_missing", document_id=str(document_id))
                return StageResult(advance=False)

            checkpoint = await _get_or_create_checkpoint(session, document_id, stage)
            if checkpoint.status is StageStatus.DONE:
                logger.info("stage_noop_done", document_id=str(document_id), stage=stage.value)
                return StageResult(advance=True)

            checkpoint.status = StageStatus.RUNNING
            checkpoint.attempt += 1
            checkpoint.started_at = datetime.now(UTC)
            checkpoint.error = None
            await session.commit()

            await publish_event(
                context.redis,
                document.loan_application_id,
                stage_event(
                    document_id=document_id,
                    stage=stage.value,
                    status=StageStatus.RUNNING.value,
                    attempt=checkpoint.attempt,
                ),
            )

        content = b""
        handler: StageHandler = handlers.HANDLERS[stage]
        try:
            async with context.session_factory() as session:
                document = await session.get(Document, document_id)
                assert document is not None
                content = await context.storage.get(document.storage_key)
                result = await handler(context, document, content)
                await session.commit()
        except Exception as exc:
            await _finish(
                context,
                document_id,
                stage,
                StageStatus.FAILED,
                error={"code": type(exc).__name__, "message": str(exc)[:500]},
            )
            raise

        status = StageStatus.FAILED if result.error else StageStatus.DONE
        await _finish(
            context,
            document_id,
            stage,
            status,
            error=result.error,
            document_status=result.document_status,
        )
        return result
    finally:
        if owns_engine and engine is not None:
            await engine.dispose()
            await context.redis.aclose()


async def _get_or_create_checkpoint(
    session: AsyncSession, document_id: UUID, stage: IngestStage
) -> DocumentStage:
    stmt = sa.select(DocumentStage).where(
        DocumentStage.document_id == document_id, DocumentStage.stage == stage
    )
    checkpoint = (await session.execute(stmt)).scalar_one_or_none()
    if checkpoint is None:
        checkpoint = DocumentStage(
            document_id=document_id, stage=stage, status=StageStatus.PENDING, attempt=0
        )
        session.add(checkpoint)
        await session.flush()
    return checkpoint


async def _finish(
    context: StageContext,
    document_id: UUID,
    stage: IngestStage,
    status: StageStatus,
    *,
    error: dict[str, Any] | None = None,
    document_status: DocumentStatus | None = None,
) -> None:
    async with context.session_factory() as session:
        document = await session.get(Document, document_id)
        stmt = sa.select(DocumentStage).where(
            DocumentStage.document_id == document_id, DocumentStage.stage == stage
        )
        checkpoint = (await session.execute(stmt)).scalar_one()
        checkpoint.status = status
        checkpoint.error = error
        checkpoint.finished_at = datetime.now(UTC)
        if document is not None and document_status is not None:
            document.status = document_status
        await session.commit()

        await publish_event(
            context.redis,
            document.loan_application_id if document else None,
            stage_event(
                document_id=document_id,
                stage=stage.value,
                status=status.value,
                attempt=checkpoint.attempt,
                error=error,
            ),
        )


def next_stage(stage: IngestStage) -> IngestStage | None:
    """The stage after ``stage`` in the chain (None at the end)."""
    index = STAGE_ORDER.index(stage)
    return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None

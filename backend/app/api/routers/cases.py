"""Case endpoints (Phase 4): CRUD, document upload, hybrid search, SSE progress.

Upload guardrails per the threat model (§15.2): per-file size cap, extension
allowlist, magic detection downstream in the worker (never in the API
process). SSE follows ADR-0014: snapshot rehydrate from ``document_stages``,
then live Redis pub/sub relay.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.providers import (
    get_settings_dep,
    get_storage,
    get_tenant_context,
    get_tenant_db_session,
    require_permissions,
)
from app.api.schemas.cases import (
    CaseCreateRequest,
    CaseResponse,
    DocumentPasswordRequest,
    DocumentResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
    StageStatusResponse,
    UploadResponse,
)
from app.core.config import Settings
from app.core.context import TenantContext
from app.core.errors import NotFoundError, ValidationFailedError
from app.domain.enums import DocumentStatus, Permission
from app.infrastructure.db.engine import tenant_session
from app.infrastructure.db.models import Document, DocumentStage
from app.infrastructure.db.repositories.audit import record_audit
from app.infrastructure.db.repositories.cases import DocumentRepository, LoanApplicationRepository
from app.infrastructure.db.retrieval import HybridRetriever
from app.infrastructure.redis.client import get_redis
from app.infrastructure.storage.local import LocalFilesystemStorage
from app.ingestion.events import case_channel, stage_event
from app.ingestion.handlers import document_password_key
from app.ingestion.stages import get_embedder
from app.tasks.ingest import enqueue_stage, start_document_pipeline

router = APIRouter(prefix="/cases", tags=["cases"])

_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".xls",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".zip",
    ".eml",
    ".msg",
    ".txt",
}
_PASSWORD_TTL_SECONDS = 3600


def _document_response(document: Document, stages: list[DocumentStage]) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        filename=document.filename,
        media_type=document.media_type,
        doc_type=document.doc_type,
        doc_type_confidence=document.doc_type_confidence,
        status=document.status,
        page_count=document.page_count,
        sha256=document.sha256,
        created_at=document.created_at,
        stages=[
            StageStatusResponse(
                stage=stage.stage, status=stage.status, attempt=stage.attempt, error=stage.error
            )
            for stage in sorted(stages, key=lambda item: item.created_at)
        ],
    )


@router.post(
    "",
    response_model=CaseResponse,
    status_code=201,
    dependencies=[Depends(require_permissions(Permission.CASE_WRITE))],
)
async def create_case(
    body: CaseCreateRequest,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> CaseResponse:
    """Open a case (loan application) for a company."""
    case = await LoanApplicationRepository(session).create(
        ctx,
        company_id=body.company_id,
        title=body.title,
        requested_amount=body.requested_amount,
        currency=body.currency,
    )
    await record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="case.create",
        entity_type="loan_application",
        entity_id=case.id,
        payload={"title": case.title},
    )
    return CaseResponse.model_validate(case.model_dump())


@router.get(
    "",
    response_model=list[CaseResponse],
    dependencies=[Depends(require_permissions(Permission.CASE_READ))],
)
async def list_cases(
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
    limit: int = 50,
    offset: int = 0,
) -> list[CaseResponse]:
    cases = await LoanApplicationRepository(session).list(
        ctx, limit=min(limit, 200), offset=max(offset, 0)
    )
    return [CaseResponse.model_validate(case.model_dump()) for case in cases]


@router.get(
    "/{case_id}",
    response_model=CaseResponse,
    dependencies=[Depends(require_permissions(Permission.CASE_READ))],
)
async def get_case(
    case_id: UUID,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> CaseResponse:
    case = await LoanApplicationRepository(session).get(ctx, case_id)
    if case is None:
        raise NotFoundError("Case not found")
    return CaseResponse.model_validate(case.model_dump())


@router.post(
    "/{case_id}/documents",
    response_model=UploadResponse,
    status_code=201,
    dependencies=[Depends(require_permissions(Permission.DOCUMENT_UPLOAD))],
)
async def upload_documents(
    case_id: UUID,
    files: list[UploadFile],
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
    storage: Annotated[LocalFilesystemStorage, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> UploadResponse:
    """Upload evidence files; each starts its ingestion pipeline."""
    case = await LoanApplicationRepository(session).get(ctx, case_id)
    if case is None:
        raise NotFoundError("Case not found")
    if not files:
        raise ValidationFailedError("No files supplied")

    max_bytes = settings.ingest.max_upload_mb * 1024 * 1024
    documents = DocumentRepository(session)
    created: list[Document] = []
    for upload in files:
        filename = upload.filename or "upload.bin"
        extension = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if extension not in _ALLOWED_EXTENSIONS:
            raise ValidationFailedError(
                f"File type {extension or '(none)'} is not allowed",
                details={"allowed": sorted(_ALLOWED_EXTENSIONS)},
            )
        content = await upload.read()
        if len(content) > max_bytes:
            raise ValidationFailedError(
                f"{filename} exceeds the {settings.ingest.max_upload_mb} MB upload cap"
            )
        blob = await storage.put(case_id=case_id, filename=filename, data=content)
        document = await documents.create(
            ctx,
            loan_application_id=case_id,
            company_id=case.company_id,
            storage_key=blob.key,
            sha256=blob.sha256,
            filename=filename,
            media_type=upload.content_type or "application/octet-stream",
        )
        created.append(document)

    await record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="documents.upload",
        entity_type="loan_application",
        entity_id=case_id,
        payload={"count": len(created), "filenames": [doc.filename for doc in created]},
    )
    await session.commit()  # documents must be visible before workers start

    for document in created:
        start_document_pipeline(document.id)

    return UploadResponse(documents=[_document_response(doc, []) for doc in created])


@router.get(
    "/{case_id}/documents",
    response_model=list[DocumentResponse],
    dependencies=[Depends(require_permissions(Permission.CASE_READ))],
)
async def list_case_documents(
    case_id: UUID,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> list[DocumentResponse]:
    repo = DocumentRepository(session)
    documents = await repo.list_by_case(ctx, case_id)
    stage_pairs = await repo.stages_for_case(ctx, case_id)
    stages_by_document: dict[UUID, list[DocumentStage]] = {}
    for document_id, stage in stage_pairs:
        stages_by_document.setdefault(document_id, []).append(stage)
    return [
        _document_response(document, stages_by_document.get(document.id, []))
        for document in documents
    ]


@router.get(
    "/{case_id}/documents/{document_id}/content",
    dependencies=[Depends(require_permissions(Permission.CASE_READ))],
)
async def download_document(
    case_id: UUID,
    document_id: UUID,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
    storage: Annotated[LocalFilesystemStorage, Depends(get_storage)],
) -> Response:
    """Stream the original bytes (the pdf.js viewer's source)."""
    document = await DocumentRepository(session).get(ctx, document_id)
    if document is None or document.loan_application_id != case_id:
        raise NotFoundError("Document not found")
    content = await storage.get(document.storage_key)
    return Response(
        content=content,
        media_type=document.media_type,
        headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
    )


@router.post(
    "/{case_id}/documents/{document_id}/password",
    status_code=202,
    dependencies=[Depends(require_permissions(Permission.DOCUMENT_UPLOAD))],
)
async def submit_document_password(
    case_id: UUID,
    document_id: UUID,
    body: DocumentPasswordRequest,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> dict[str, str]:
    """Provide a password for a NEEDS_PASSWORD PDF and resume its pipeline.

    The password lives only in Redis with a short TTL (never persisted) —
    §15.6 PII handling.
    """
    document = await DocumentRepository(session).get(ctx, document_id)
    if document is None or document.loan_application_id != case_id:
        raise NotFoundError("Document not found")
    if document.status is not DocumentStatus.NEEDS_PASSWORD:
        raise ValidationFailedError("Document is not waiting for a password")

    redis = get_redis()
    await redis.set(document_password_key(document_id), body.password, ex=_PASSWORD_TTL_SECONDS)

    from app.domain.enums import IngestStage  # local: single-use import

    enqueue_stage(document_id, IngestStage.DETECT_TYPE)
    return {"status": "resumed"}


@router.post(
    "/{case_id}/search",
    response_model=SearchResponse,
    dependencies=[Depends(require_permissions(Permission.CASE_READ))],
)
async def search_case(
    case_id: UUID,
    body: SearchRequest,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_db_session)],
) -> SearchResponse:
    """Hybrid retrieval (FTS + vector + RRF) over the case's evidence."""
    case = await LoanApplicationRepository(session).get(ctx, case_id)
    if case is None:
        raise NotFoundError("Case not found")

    embedder = get_embedder()
    query_vector = await asyncio.to_thread(embedder.embed, [body.query])
    hits = await HybridRetriever(session).retrieve(
        org_id=ctx.org_id,
        query_text=body.query,
        query_vector=query_vector[0],
        loan_application_id=case_id,
        doc_types=body.doc_types,
        top_k=body.top_k,
    )
    return SearchResponse(
        hits=[
            SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                doc_type=hit.doc_type,
                text=hit.text,
                page_start=hit.page_start,
                page_end=hit.page_end,
                rrf_score=hit.rrf_score,
                fts_rank=hit.fts_rank,
                vector_rank=hit.vector_rank,
            )
            for hit in hits
        ]
    )


@router.get("/{case_id}/events")
async def case_events(
    case_id: UUID,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> StreamingResponse:
    """SSE progress stream: stage snapshot first, then live pub/sub relay.

    Reconnect semantics per ADR-0014: the snapshot IS the rehydrate (state
    lives in ``document_stages``); Redis pub/sub carries live deltas only.
    The RLS session is scoped to the snapshot and closed BEFORE streaming —
    an SSE client must never pin a database transaction open.
    """
    async with tenant_session(ctx) as session:
        case = await LoanApplicationRepository(session).get(ctx, case_id)
        if case is None:
            raise NotFoundError("Case not found")
        stage_pairs = await DocumentRepository(session).stages_for_case(ctx, case_id)
        snapshot = [
            stage_event(
                document_id=document_id,
                stage=stage.stage.value,
                status=stage.status.value,
                attempt=stage.attempt,
                error=stage.error,
            )
            for document_id, stage in stage_pairs
        ]

    async def stream() -> AsyncIterator[str]:
        for event in snapshot:
            yield f"data: {json.dumps(event)}\n\n"
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(case_channel(case_id))
        try:
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message is None:
                    yield ": keepalive\n\n"
                    continue
                payload = message["data"]
                text = payload.decode() if isinstance(payload, bytes) else str(payload)
                yield f"data: {text}\n\n"
        finally:
            await pubsub.unsubscribe(case_channel(case_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

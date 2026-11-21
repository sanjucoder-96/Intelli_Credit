"""The nine stage handlers (ARCHITECTURE §7).

Idempotency pattern: every handler that persists rows is a deterministic
overwrite — delete-what-this-document-produced, then insert — so a crashed
and retried stage converges instead of duplicating (§15.3). Every Gemini
call lands in the ``llm_interactions`` ledger (ADR-0012). Handlers never
enqueue Celery work themselves — fan-out (ZIP children) is returned to the
task shell, keeping this module import-clean of the tasks layer.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from email import policy
from email.parser import BytesParser
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.core.logging import get_logger
from app.docintel.extractors.bank_statement import (
    extract_bank_statement_csv,
    extract_bank_statement_rows,
    extract_bank_statement_xlsx,
)
from app.docintel.extractors.gstr2b import extract_gstr2b
from app.docintel.extractors.gstr3b import extract_gstr3b
from app.docintel.registry import ExtractionStrategy, document_type_registry
from app.docintel.schemas import (
    BankStatementExtract,
    DocumentClassification,
    ExtractionResult,
    Gstr2bExtract,
    Gstr3bExtract,
    ItrExtract,
)
from app.docintel.validators import CaseExtracts, run_all
from app.domain.enums import (
    DocumentStatus,
    EvidenceKind,
    ExecutionStatus,
    FindingKind,
    IngestStage,
)
from app.infrastructure.db.models import (
    Chunk,
    Document,
    Evidence,
    ExtractedField,
    Finding,
    LLMInteraction,
)
from app.infrastructure.gemini import GeminiCallResult, GeminiUnconfiguredError
from app.ingestion import pdf as pdf_utils
from app.ingestion.artifacts import (
    ChunksArtifact,
    PageRecord,
    PagesArtifact,
    TablesArtifact,
    chunks_key,
    extraction_key,
    page_image_key,
    pages_key,
    read_json_or_none,
    tables_key,
    write_json,
)
from app.ingestion.chunker import chunk_pages
from app.ingestion.ocr import gemini_vision_page, rapidocr_page
from app.ingestion.stages import StageContext, StageResult
from app.ingestion.tables import extract_tables_born_digital

logger = get_logger("app.ingestion.handlers")

_VALIDATOR_AGENT = "cross_validator"

_EXTRACT_MODELS: dict[str, type[Any]] = {
    "gstr3b": Gstr3bExtract,
    "gstr2b": Gstr2bExtract,
    "gstr2a": Gstr2bExtract,
    "bank_statement": BankStatementExtract,
    "itr": ItrExtract,
}


def document_password_key(document_id: UUID) -> str:
    """Redis key the API stores an uploaded PDF password under (short TTL)."""
    return f"doc:{document_id}:password"


async def _get_password(context: StageContext, document_id: UUID) -> str | None:
    value = await context.redis.get(document_password_key(document_id))
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


async def _record_llm(
    session: AsyncSession,
    document: Document,
    call: GeminiCallResult,
    *,
    prompt: str,
    error: str | None = None,
) -> None:
    """Ledger row for one Gemini call (ADR-0012 — cost accounting is real)."""
    session.add(
        LLMInteraction(
            org_id=document.org_id,
            model=call.model,
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            tokens_in=call.tokens_in,
            tokens_out=call.tokens_out,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd,
            status=ExecutionStatus.ERROR if error else ExecutionStatus.OK,
            error=error,
        )
    )


# --------------------------------------------------------------- detect_type
async def detect_type(context: StageContext, document: Document, content: bytes) -> StageResult:
    """Magic-byte/extension detection; ZIP + EML fan-out; encryption gate."""
    import puremagic

    filename = document.filename.lower()
    media_type = document.media_type
    try:
        detected = puremagic.from_string(content, mime=True, filename=document.filename)
        if detected:
            media_type = detected
    except puremagic.PureError:
        pass

    if filename.endswith(".zip") or media_type == "application/zip":
        children = await _expand_zip(context, document, content)
        async with context.session_factory() as session:
            db_document = await session.get(Document, document.id)
            assert db_document is not None
            db_document.media_type = "application/zip"
            db_document.status = DocumentStatus.READY  # container itself is done
            await session.commit()
        return StageResult(advance=False, extra_documents=children)

    if filename.endswith(".eml") or media_type == "message/rfc822":
        children = await _expand_eml(context, document, content)
        async with context.session_factory() as session:
            db_document = await session.get(Document, document.id)
            assert db_document is not None
            db_document.media_type = "message/rfc822"
            db_document.status = DocumentStatus.READY
            await session.commit()
        return StageResult(advance=False, extra_documents=children)

    if media_type == "application/pdf" or filename.endswith(".pdf"):
        password = await _get_password(context, document.id)
        if password is None and pdf_utils.is_encrypted_without_password(content):
            return StageResult(
                advance=False,
                document_status=DocumentStatus.NEEDS_PASSWORD,
                error={"code": "needs_password", "message": "PDF is password-protected"},
            )
        media_type = "application/pdf"

    async with context.session_factory() as session:
        db_document = await session.get(Document, document.id)
        assert db_document is not None
        db_document.media_type = media_type
        db_document.status = DocumentStatus.PROCESSING
        await session.commit()
    return StageResult(advance=True)


async def _expand_zip(context: StageContext, document: Document, content: bytes) -> list[UUID]:
    """Create child documents from a ZIP (depth + cumulative-size guards)."""
    settings = context.settings.ingest

    async with context.session_factory() as session:
        depth = 0
        ancestor = document.parent_id
        while ancestor is not None:
            depth += 1
            parent = await session.get(Document, ancestor)
            ancestor = parent.parent_id if parent else None
        if depth >= settings.zip_max_depth:
            raise ValidationFailedError(
                f"ZIP nesting exceeds the depth cap ({settings.zip_max_depth})"
            )

    children: list[UUID] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename.startswith("__MACOSX"):
                continue
            total += info.file_size
            if total > settings.zip_max_total_mb * 1024 * 1024:
                raise ValidationFailedError(
                    f"ZIP expands beyond {settings.zip_max_total_mb} MB (zip-bomb guard)"
                )
            payload = archive.read(info)
            blob = await context.storage.put(
                case_id=document.loan_application_id, filename=info.filename, data=payload
            )
            async with context.session_factory() as session:
                child = Document(
                    org_id=document.org_id,
                    loan_application_id=document.loan_application_id,
                    company_id=document.company_id,
                    parent_id=document.id,
                    storage_key=blob.key,
                    sha256=blob.sha256,
                    filename=info.filename,
                    media_type="application/octet-stream",
                    data_origin=document.data_origin,
                )
                session.add(child)
                await session.commit()
                children.append(child.id)
    return children


async def _expand_eml(context: StageContext, document: Document, content: bytes) -> list[UUID]:
    """Body becomes a text child; attachments fan out as children."""
    message = BytesParser(policy=policy.default).parsebytes(content)
    children: list[UUID] = []

    body_part = message.get_body(preferencelist=("plain", "html"))
    body_text = body_part.get_content() if body_part else ""
    if body_text.strip():
        blob = await context.storage.put(
            case_id=document.loan_application_id,
            filename=f"{document.filename}.body.txt",
            data=str(body_text).encode("utf-8"),
        )
        async with context.session_factory() as session:
            child = Document(
                org_id=document.org_id,
                loan_application_id=document.loan_application_id,
                company_id=document.company_id,
                parent_id=document.id,
                storage_key=blob.key,
                sha256=blob.sha256,
                filename=f"{document.filename}.body.txt",
                media_type="text/plain",
                data_origin=document.data_origin,
            )
            session.add(child)
            await session.commit()
            children.append(child.id)

    for part in message.iter_attachments():
        payload = part.get_payload(decode=True)
        if not payload or not isinstance(payload, bytes):
            continue
        name = part.get_filename() or "attachment.bin"
        blob = await context.storage.put(
            case_id=document.loan_application_id, filename=name, data=payload
        )
        async with context.session_factory() as session:
            child = Document(
                org_id=document.org_id,
                loan_application_id=document.loan_application_id,
                company_id=document.company_id,
                parent_id=document.id,
                storage_key=blob.key,
                sha256=blob.sha256,
                filename=name,
                media_type=part.get_content_type(),
                data_origin=document.data_origin,
            )
            session.add(child)
            await session.commit()
            children.append(child.id)
    return children


# -------------------------------------------------------- extract_text_layer
async def extract_text_layer(
    context: StageContext, document: Document, content: bytes
) -> StageResult:
    """Per-page text + OCR-candidate scoring; text files become one page."""
    settings = context.settings.ingest

    if document.media_type == "application/pdf":
        password = await _get_password(context, document.id)
        try:
            page_texts = pdf_utils.extract_text_layer(
                content, password=password, density_threshold=settings.ocr_text_density_threshold
            )
        except pdf_utils.PdfPasswordRequiredError:
            return StageResult(
                advance=False,
                document_status=DocumentStatus.NEEDS_PASSWORD,
                error={"code": "needs_password", "message": "PDF is password-protected"},
            )
        artifact = PagesArtifact(
            pages=[
                PageRecord(
                    page=entry.page,
                    text=entry.text,
                    density=entry.density,
                    needs_ocr=entry.density < settings.ocr_text_density_threshold,
                )
                for entry in page_texts
            ]
        )
        async with context.session_factory() as session:
            db_document = await session.get(Document, document.id)
            assert db_document is not None
            db_document.page_count = len(artifact.pages)
            await session.commit()
    elif document.media_type.startswith("text/"):
        artifact = PagesArtifact(
            pages=[PageRecord(page=1, text=content.decode("utf-8", errors="replace"), density=1.0)]
        )
    else:
        artifact = PagesArtifact()  # spreadsheets are handled natively downstream

    await write_json(context.storage, pages_key(document.id), artifact.model_dump())
    return StageResult(advance=True)


# ------------------------------------------------------------------ ocr_pages
async def ocr_pages(context: StageContext, document: Document, content: bytes) -> StageResult:
    """RapidOCR primary; Gemini Flash vision escalation below the threshold."""
    raw = await read_json_or_none(context.storage, pages_key(document.id))
    if raw is None:
        return StageResult(advance=True)
    artifact = PagesArtifact.model_validate(raw)
    needs = [record for record in artifact.pages if record.needs_ocr]
    if not needs or document.media_type != "application/pdf":
        return StageResult(advance=True)

    settings = context.settings.ingest
    password = await _get_password(context, document.id)
    for record in needs:
        png = pdf_utils.render_page_png(
            content, record.page, dpi=settings.page_render_dpi, password=password
        )
        image_blob = await context.storage.put_derived(
            key=page_image_key(document.id, record.page), data=png
        )
        record.image_key = image_blob.key

        ocr_result = rapidocr_page(png)
        record.text = ocr_result.text
        record.ocr_engine = ocr_result.engine
        record.ocr_mean_confidence = round(ocr_result.mean_confidence, 4)

        if (
            ocr_result.mean_confidence < settings.ocr_line_confidence_threshold
            and context.gemini.configured
        ):
            prompt = "vision-ocr"
            try:
                vision_result, call = await gemini_vision_page(context.gemini, png)
            except GeminiUnconfiguredError:
                pass
            else:
                async with context.session_factory() as session:
                    await _record_llm(session, document, call, prompt=prompt)
                    await session.commit()
                if vision_result.lines:
                    record.text = vision_result.text
                    record.ocr_engine = vision_result.engine
                    record.ocr_mean_confidence = round(vision_result.mean_confidence, 4)

    await write_json(context.storage, pages_key(document.id), artifact.model_dump())
    return StageResult(advance=True)


# -------------------------------------------------------------- layout_tables
async def layout_tables(context: StageContext, document: Document, content: bytes) -> StageResult:
    """pdfplumber grids for born-digital pages.

    Scanned-table reconstruction via Gemini vision is a documented Phase-5
    seam — Phase 4 relies on OCR text lines for scanned statutory forms.
    """
    if document.media_type != "application/pdf":
        await write_json(context.storage, tables_key(document.id), TablesArtifact().model_dump())
        return StageResult(advance=True)

    raw = await read_json_or_none(context.storage, pages_key(document.id))
    artifact = PagesArtifact.model_validate(raw) if raw else PagesArtifact()
    born_digital = [record.page for record in artifact.pages if not record.needs_ocr]
    grids = extract_tables_born_digital(content, born_digital) if born_digital else []
    await write_json(
        context.storage, tables_key(document.id), TablesArtifact(tables=grids).model_dump()
    )
    return StageResult(advance=True)


# ------------------------------------------------------------------- classify
async def classify(context: StageContext, document: Document, content: bytes) -> StageResult:
    """Tier-1 regex anchors; tier-2 Gemini Flash constrained to the taxonomy."""
    raw = await read_json_or_none(context.storage, pages_key(document.id))
    artifact = PagesArtifact.model_validate(raw) if raw else PagesArtifact()
    head_text = "\n".join(record.text for record in artifact.pages[:2])

    doc_type: str | None = None
    confidence = 0.0

    if head_text.strip():
        tier1 = document_type_registry.classify_tier1(head_text)
        if tier1:
            doc_type, confidence = tier1

    if doc_type is None and document.media_type in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    ):
        # Spreadsheet heuristic: a bank-statement header row is decisive.
        probe = (
            extract_bank_statement_xlsx(content)
            if document.media_type.endswith("sheet")
            else extract_bank_statement_csv(content)
        )
        if not any(issue.severity == "error" for issue in probe.issues):
            doc_type, confidence = "bank_statement", 0.9

    if doc_type is None and head_text.strip() and context.gemini.configured:
        taxonomy = ", ".join(document_type_registry.types())
        prompt = (
            "Classify this document into exactly one of the following types: "
            f"{taxonomy}. Document beginning:\n\n{head_text[:4000]}"
        )
        classification, call = await context.gemini.generate_structured(
            schema=DocumentClassification, prompt=prompt
        )
        async with context.session_factory() as session:
            await _record_llm(session, document, call, prompt=prompt)
            await session.commit()
        if classification.doc_type in document_type_registry.types():
            doc_type, confidence = classification.doc_type, min(classification.confidence, 0.9)

    async with context.session_factory() as session:
        db_document = await session.get(Document, document.id)
        assert db_document is not None
        db_document.doc_type = doc_type or "unclassified"
        db_document.doc_type_confidence = confidence if doc_type else None
        await session.commit()
    return StageResult(advance=True)


# -------------------------------------------------------------- extract_domain
async def extract_domain(context: StageContext, document: Document, content: bytes) -> StageResult:
    """Typed extraction per the registry strategy; deterministic overwrite."""
    async with context.session_factory() as session:
        db_document = await session.get(Document, document.id)
        assert db_document is not None
        doc_type = db_document.doc_type

    spec = document_type_registry.get(doc_type)
    if spec.extraction is ExtractionStrategy.NONE:
        return StageResult(advance=True)

    raw_pages = await read_json_or_none(context.storage, pages_key(document.id))
    pages = PagesArtifact.model_validate(raw_pages) if raw_pages else PagesArtifact()
    raw_tables = await read_json_or_none(context.storage, tables_key(document.id))
    tables = TablesArtifact.model_validate(raw_tables) if raw_tables else TablesArtifact()

    result: ExtractionResult | None = None
    if spec.extraction is ExtractionStrategy.DETERMINISTIC:
        if doc_type == "gstr3b":
            result = extract_gstr3b(pages.full_text())
        elif doc_type in ("gstr2b", "gstr2a"):
            result = extract_gstr2b(pages.full_text(), tables.tables)
        elif doc_type == "bank_statement":
            result = _extract_bank(document, content, pages, tables)
    elif spec.extraction is ExtractionStrategy.LLM:
        if not context.gemini.configured:
            return StageResult(
                advance=True,  # searchability still matters: chunk/embed continue
                error={
                    "code": "gemini_unconfigured",
                    "message": f"{doc_type} extraction requires IC_GEMINI__API_KEY",
                },
            )
        assert spec.llm_schema is not None
        text = "\n".join(pages.full_text())[:24000]
        prompt = f"{spec.llm_instruction}\n\nDocument text:\n\n{text}"
        data, call = await context.gemini.generate_structured(
            schema=spec.llm_schema, prompt=prompt, model=context.settings.gemini.pro_model
        )
        async with context.session_factory() as session:
            db_document = await session.get(Document, document.id)
            assert db_document is not None
            await _record_llm(session, db_document, call, prompt=prompt)
            await session.commit()
        result = ExtractionResult(
            schema_id=doc_type,
            schema_version="1",
            data=data,
            field_confidence=dict.fromkeys(data.model_dump(exclude_none=True), 0.7),
        )

    if result is None:
        return StageResult(advance=True)

    await write_json(
        context.storage,
        extraction_key(document.id),
        {
            "schema_id": result.schema_id,
            "schema_version": result.schema_version,
            "data": result.data.model_dump(mode="json"),
        },
    )

    async with context.session_factory() as session:
        await session.execute(
            sa.delete(ExtractedField).where(ExtractedField.document_id == document.id)
        )
        for field_path, value in result.flatten().items():
            citation = result.citations.get(field_path)
            session.add(
                ExtractedField(
                    org_id=document.org_id,
                    document_id=document.id,
                    schema_id=result.schema_id,
                    schema_version=result.schema_version,
                    field_path=field_path,
                    value={"value": value},
                    confidence=result.field_confidence.get(field_path),
                    page=citation.page if citation else None,
                    bbox=citation.model_dump(mode="json") if citation else None,
                )
            )
        await session.commit()

    error = None
    hard_errors = [issue for issue in result.issues if issue.severity == "error"]
    if hard_errors:
        error = {"code": "extraction_incomplete", "message": hard_errors[0].message}
    return StageResult(advance=True, error=error)


def _extract_bank(
    document: Document, content: bytes, pages: PagesArtifact, tables: TablesArtifact
) -> ExtractionResult:
    if document.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return extract_bank_statement_xlsx(content)
    if document.media_type == "text/csv" or document.filename.lower().endswith(".csv"):
        return extract_bank_statement_csv(content)
    rows = [row for _page, grid in tables.tables for row in grid]
    header_text = "\n".join(pages.full_text()[:1])
    return extract_bank_statement_rows(rows, header_text=header_text, cite="table")


# ----------------------------------------------------------------------- chunk
async def chunk(context: StageContext, document: Document, content: bytes) -> StageResult:
    """Chunk pages + tables under the type's profile (spreadsheets summarize)."""
    async with context.session_factory() as session:
        db_document = await session.get(Document, document.id)
        assert db_document is not None
        doc_type = db_document.doc_type
    spec = document_type_registry.get(doc_type)

    raw_pages = await read_json_or_none(context.storage, pages_key(document.id))
    pages = PagesArtifact.model_validate(raw_pages) if raw_pages else PagesArtifact()
    raw_tables = await read_json_or_none(context.storage, tables_key(document.id))
    tables = TablesArtifact.model_validate(raw_tables) if raw_tables else TablesArtifact()

    if pages.pages or tables.tables:
        records = chunk_pages(pages, tables.tables, spec.chunking, context.settings.ingest)
    else:
        records = _chunks_from_extraction(
            await read_json_or_none(context.storage, extraction_key(document.id))
        )

    await write_json(
        context.storage, chunks_key(document.id), ChunksArtifact(chunks=records).model_dump()
    )
    return StageResult(advance=True)


def _chunks_from_extraction(extraction: dict[str, Any] | None) -> list[Any]:
    """Spreadsheet statements: searchable summary chunks from the extraction."""
    from app.ingestion.artifacts import ChunkRecord

    if not extraction:
        return []
    data = extraction.get("data", {})
    lines = [f"{extraction.get('schema_id')} extraction summary"]
    for month in data.get("monthly", []) or []:
        lines.append(
            f"period {month.get('period')}: credits {month.get('credits')}, "
            f"debits {month.get('debits')}, cheque returns {month.get('cheque_returns')}"
        )
    for txn in (data.get("transactions", []) or [])[:200]:
        lines.append(
            f"{txn.get('txn_date')} {txn.get('narration')} "
            f"dr={txn.get('debit')} cr={txn.get('credit')}"
        )
    text = "\n".join(lines)
    return [ChunkRecord(text=text, chunk_kind="kv")] if len(lines) > 1 else []


# ------------------------------------------------------------------ embed_index
async def embed_index(context: StageContext, document: Document, content: bytes) -> StageResult:
    """Embed + index chunks (deterministic overwrite); document goes READY."""
    raw = await read_json_or_none(context.storage, chunks_key(document.id))
    artifact = ChunksArtifact.model_validate(raw) if raw else ChunksArtifact()

    vectors = context.embedder.embed([record.text for record in artifact.chunks])

    async with context.session_factory() as session:
        await session.execute(sa.delete(Chunk).where(Chunk.document_id == document.id))
        for record, vector in zip(artifact.chunks, vectors, strict=True):
            session.add(
                Chunk(
                    org_id=document.org_id,
                    document_id=document.id,
                    loan_application_id=document.loan_application_id,
                    doc_type=document.doc_type,
                    chunk_kind=record.chunk_kind,
                    text=record.text,
                    section_path=record.section_path,
                    page_start=record.page_start,
                    page_end=record.page_end,
                    embedding=vector,
                )
            )
        db_document = await session.get(Document, document.id)
        assert db_document is not None
        db_document.status = DocumentStatus.READY
        await session.commit()
    return StageResult(advance=True)


# --------------------------------------------------------------- cross_validate
async def cross_validate(context: StageContext, document: Document, content: bytes) -> StageResult:
    """Case-wide validators; findings + evidence are a deterministic recompute."""
    case_id = document.loan_application_id
    if case_id is None:
        return StageResult(advance=True)

    async with context.session_factory() as session:
        result = await session.execute(
            sa.select(Document).where(Document.loan_application_id == case_id)
        )
        case_documents = list(result.scalars())

    extracts = CaseExtracts()
    for case_document in case_documents:
        extraction = await read_json_or_none(context.storage, extraction_key(case_document.id))
        if not extraction:
            continue
        model_cls = _EXTRACT_MODELS.get(extraction.get("schema_id", ""))
        if model_cls is None:
            continue
        model = model_cls.model_validate(extraction["data"])
        if isinstance(model, Gstr3bExtract):
            extracts.gstr3b.append((case_document.id, model))
        elif isinstance(model, Gstr2bExtract):
            extracts.gstr2b.append((case_document.id, model))
        elif isinstance(model, BankStatementExtract):
            extracts.bank_statements.append((case_document.id, model))
        elif isinstance(model, ItrExtract):
            extracts.itr.append((case_document.id, model))

    outcomes = run_all(extracts)

    async with context.session_factory() as session:
        # Deterministic recompute: replace this validator's prior output.
        await session.execute(
            sa.delete(Evidence).where(
                Evidence.loan_application_id == case_id,
                Evidence.created_by_agent == _VALIDATOR_AGENT,
            )
        )
        await session.execute(
            sa.delete(Finding).where(
                Finding.loan_application_id == case_id,
                Finding.kind == FindingKind.VALIDATION,
            )
        )
        for outcome in outcomes:
            evidence_ids: list[str] = []
            for cited_document_id, citation in outcome.citations:
                evidence = Evidence(
                    org_id=document.org_id,
                    loan_application_id=case_id,
                    kind=EvidenceKind.DOCUMENT,
                    claim=outcome.title,
                    document_id=cited_document_id,
                    page=citation.page,
                    quote=citation.quote,
                    source_tier=1.0,
                    confidence=0.9,
                    created_by_agent=_VALIDATOR_AGENT,
                )
                session.add(evidence)
                await session.flush()
                evidence_ids.append(str(evidence.id))
            session.add(
                Finding(
                    org_id=document.org_id,
                    loan_application_id=case_id,
                    agent=_VALIDATOR_AGENT,
                    kind=FindingKind.VALIDATION,
                    category=outcome.category,
                    severity=outcome.severity,
                    title=outcome.title,
                    narrative=outcome.narrative,
                    data={
                        "validator_id": outcome.validator_id,
                        "metrics": outcome.metrics,
                        "period": outcome.period,
                        "evidence_ids": evidence_ids,
                    },
                    confidence=0.9,
                    source_tier=1.0,
                )
            )
        await session.commit()

    logger.info("cross_validation_complete", case_id=str(case_id), findings=len(outcomes))
    return StageResult(advance=True)


# Handlers share one signature (context, document, content) even where a
# stage ignores the raw bytes — uniformity keeps dispatch and retries trivial.
HANDLERS: dict[IngestStage, Any] = {
    IngestStage.DETECT_TYPE: detect_type,
    IngestStage.EXTRACT_TEXT_LAYER: extract_text_layer,
    IngestStage.OCR_PAGES: ocr_pages,
    IngestStage.LAYOUT_TABLES: layout_tables,
    IngestStage.CLASSIFY: classify,
    IngestStage.EXTRACT_DOMAIN: extract_domain,
    IngestStage.CHUNK: chunk,
    IngestStage.EMBED_INDEX: embed_index,
    IngestStage.CROSS_VALIDATE: cross_validate,
}

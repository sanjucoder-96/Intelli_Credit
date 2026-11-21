"""Phase-4 acceptance: mixed bundle in → READY documents with typed,
cited extractions, cross-document findings, indexed chunks — and a killed
worker resumes without duplicates (ROADMAP Phase 4 exit criteria).

Runs the real stage handlers inline (the same code the Celery shells call)
against live Postgres/Redis. The embedding provider is injected at the
StageContext seam with a deterministic hash embedder — retrieval mechanics
are fully exercised without a model download; the real fastembed provider is
covered by the live-stack smoke (see PHASE4_REPORT.md).
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.context import TenantContext
from app.domain.enums import (
    DataOrigin,
    DocumentStatus,
    IngestStage,
    Role,
    StageStatus,
)
from app.infrastructure.db.models import (
    Chunk,
    Company,
    Document,
    DocumentStage,
    Evidence,
    ExtractedField,
    Finding,
    LoanApplication,
    Organization,
)
from app.infrastructure.db.retrieval import HybridRetriever
from app.infrastructure.gemini import GeminiClient
from app.infrastructure.redis.client import get_redis
from app.infrastructure.storage.local import LocalFilesystemStorage
from app.ingestion.artifacts import pages_key, read_json_or_none
from app.ingestion.stages import STAGE_ORDER, StageContext, next_stage, run_stage
from tests import fixtures_gen
from tests.support import admin_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres/Redis required — set IC_INTEGRATION_TESTS=1",
    ),
]

DIM = 384


class HashEmbedder:
    """Deterministic EmbeddingProviderPort test double (injected at the port)."""

    @property
    def dimension(self) -> int:
        return DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            seed = hashlib.sha256(text.encode()).digest()
            raw = [((seed[index % 32] << (index // 32)) % 251) / 251 - 0.5 for index in range(DIM)]
            norm = math.sqrt(sum(value * value for value in raw)) or 1.0
            vectors.append([value / norm for value in raw])
        return vectors


async def _seed_case(engine: AsyncEngine) -> tuple[TenantContext, uuid.UUID]:
    factory = session_factory(engine)
    async with factory() as session:
        suffix = uuid.uuid4().hex[:8]
        org = Organization(name=f"P4 Acceptance {suffix}", slug=f"p4-{suffix}", is_active=True)
        session.add(org)
        await session.flush()
        company = Company(
            org_id=org.id,
            legal_name=f"Acme Textiles {suffix}",
            gstin="27AAACA1234A1Z5",
            data_origin=DataOrigin.SYNTHETIC_DEMO,
        )
        session.add(company)
        await session.flush()
        case = LoanApplication(
            org_id=org.id, company_id=company.id, title=f"P4 acceptance case {suffix}"
        )
        session.add(case)
        await session.commit()
        ctx = TenantContext(org_id=org.id, user_id=uuid.uuid4(), role=Role.ANALYST)
        return ctx, case.id


async def _register_document(
    context: StageContext,
    ctx: TenantContext,
    case_id: uuid.UUID,
    filename: str,
    content: bytes,
) -> uuid.UUID:
    blob = await context.storage.put(case_id=case_id, filename=filename, data=content)
    async with context.session_factory() as session:
        document = Document(
            org_id=ctx.org_id,
            loan_application_id=case_id,
            storage_key=blob.key,
            sha256=blob.sha256,
            filename=filename,
            media_type="application/octet-stream",
            data_origin=DataOrigin.SYNTHETIC_DEMO,
        )
        session.add(document)
        await session.commit()
        return document.id


async def _run_pipeline(context: StageContext, document_id: uuid.UUID) -> None:
    """Drive the chain exactly as the Celery shells do, including fan-out."""
    stage: IngestStage | None = STAGE_ORDER[0]
    while stage is not None:
        result = await run_stage(document_id, stage, context=context)
        for child in result.extra_documents or []:
            await _run_pipeline(context, child)
        if not result.advance:
            return
        stage = next_stage(stage)


@pytest.fixture
async def pipeline_env(tmp_path: Path):  # type: ignore[no-untyped-def]
    async with admin_engine() as engine:
        ctx, case_id = await _seed_case(engine)
        context = StageContext(
            settings=get_settings(),
            session_factory=session_factory(engine),
            storage=LocalFilesystemStorage(tmp_path / "storage"),
            gemini=GeminiClient(get_settings().gemini),
            embedder=HashEmbedder(),
            redis=get_redis(),
        )
        yield engine, context, ctx, case_id


async def test_mixed_bundle_end_to_end(pipeline_env) -> None:  # type: ignore[no-untyped-def]
    engine, context, ctx, case_id = pipeline_env
    factory = session_factory(engine)

    uploads = {
        "gstr3b_april.pdf": fixtures_gen.gstr3b_born_digital(),
        "gstr3b_may_scan.pdf": fixtures_gen.gstr3b_scanned(),
        "bank_statement.xlsx": fixtures_gen.bank_statement_xlsx(),
        "bundle.zip": fixtures_gen.bundle_zip(),
    }
    ids: dict[str, uuid.UUID] = {}
    for filename, content in uploads.items():
        ids[filename] = await _register_document(context, ctx, case_id, filename, content)
        await _run_pipeline(context, ids[filename])

    async with factory() as session:
        documents = list(
            (
                await session.execute(
                    sa.select(Document).where(Document.loan_application_id == case_id)
                )
            ).scalars()
        )
        by_name = {document.filename: document for document in documents}

        # Every document lands READY (children included); the ZIP container too.
        assert len(documents) == 6  # 4 uploads + 2 ZIP children
        for document in documents:
            assert document.status is DocumentStatus.READY, (
                document.filename,
                document.status,
            )

        # Classification: statutory forms hit tier-1 anchors; XLSX via sniff.
        assert by_name["gstr3b_april.pdf"].doc_type == "gstr3b"
        assert by_name["gstr3b_may_scan.pdf"].doc_type == "gstr3b"
        assert by_name["bank_statement.xlsx"].doc_type == "bank_statement"
        assert by_name["gstr2b_april.pdf"].doc_type == "gstr2b"

        # Typed extraction with citations (born-digital GSTR-3B).
        fields = {
            row.field_path: row
            for row in (
                await session.execute(
                    sa.select(ExtractedField).where(
                        ExtractedField.document_id == ids["gstr3b_april.pdf"]
                    )
                )
            ).scalars()
        }
        outward = fields["outward_taxable_value"]
        assert Decimal(str(outward.value["value"])) == Decimal("10000000.00")
        assert outward.page == 1 and outward.bbox is not None  # citation present
        assert outward.confidence is not None and outward.confidence >= 0.9

        # The scanned document went down the OCR path (RapidOCR engine).
        pages = await read_json_or_none(context.storage, pages_key(ids["gstr3b_may_scan.pdf"]))
        assert pages is not None
        scanned_pages = [page for page in pages["pages"] if page["needs_ocr"]]
        assert scanned_pages, "scanned fixture must trigger the OCR path"
        assert all(page["ocr_engine"] == "rapidocr" for page in scanned_pages)
        assert all(page["image_key"] for page in scanned_pages)  # viewer page images

        # Chunks indexed with embeddings for every content document.
        chunk_counts = dict(
            (
                await session.execute(
                    sa.select(Chunk.document_id, sa.func.count())
                    .where(Chunk.loan_application_id == case_id)
                    .group_by(Chunk.document_id)
                )
            ).all()
        )
        for name in ("gstr3b_april.pdf", "bank_statement.xlsx", "gstr2b_april.pdf"):
            assert chunk_counts.get(ids.get(name) or by_name[name].id, 0) > 0, name

        # Cross-document validators fired with evidence into BOTH documents.
        findings = list(
            (
                await session.execute(
                    sa.select(Finding).where(Finding.loan_application_id == case_id)
                )
            ).scalars()
        )
        fired = {finding.data["validator_id"] for finding in findings}
        assert "gst_vs_bank_credits" in fired
        assert "gstr2b_vs_3b_itc_mismatch" in fired
        assert "cheque_return_frequency" in fired

        # The scanned May 3B fires its own gap finding too (OCR-extracted
        # numbers feed the validators end-to-end); assert April's explicitly.
        gap_finding = next(
            finding
            for finding in findings
            if finding.data["validator_id"] == "gst_vs_bank_credits"
            and finding.data.get("period") == "2026-04"
        )
        evidence_rows = list(
            (
                await session.execute(
                    sa.select(Evidence).where(
                        Evidence.id.in_(
                            [uuid.UUID(eid) for eid in gap_finding.data["evidence_ids"]]
                        )
                    )
                )
            ).scalars()
        )
        cited_documents = {row.document_id for row in evidence_rows}
        assert ids["gstr3b_april.pdf"] in cited_documents
        assert ids["bank_statement.xlsx"] in cited_documents

    # Hybrid search over the case finds the GSTR chunk (FTS arm).
    async with factory() as session:
        hits = await HybridRetriever(session).retrieve(
            org_id=ctx.org_id,
            query_text="outward taxable supplies",
            query_vector=HashEmbedder().embed(["outward taxable supplies"])[0],
            loan_application_id=case_id,
            top_k=5,
        )
        assert hits and any("Outward taxable supplies" in hit.text for hit in hits)


async def test_killed_worker_resumes_without_duplicates(pipeline_env) -> None:  # type: ignore[no-untyped-def]
    engine, context, ctx, case_id = pipeline_env
    factory = session_factory(engine)

    document_id = await _register_document(
        context, ctx, case_id, "gstr3b_april.pdf", fixtures_gen.gstr3b_born_digital()
    )
    await _run_pipeline(context, document_id)

    async def counts() -> tuple[int, int]:
        async with factory() as session:
            field_count = (
                await session.execute(
                    sa.select(sa.func.count()).where(ExtractedField.document_id == document_id)
                )
            ).scalar_one()
            chunk_count = (
                await session.execute(
                    sa.select(sa.func.count()).where(Chunk.document_id == document_id)
                )
            ).scalar_one()
            return int(field_count), int(chunk_count)

    baseline = await counts()
    assert baseline[0] > 0 and baseline[1] > 0

    # Redelivery of a DONE stage no-ops (at-least-once made safe).
    for stage in (IngestStage.EXTRACT_DOMAIN, IngestStage.EMBED_INDEX):
        result = await run_stage(document_id, stage, context=context)
        assert result.advance
    assert await counts() == baseline

    # Crash mid-stage: checkpoint left RUNNING (never DONE) → the retry
    # re-executes the handler; deterministic overwrites converge.
    async with factory() as session:
        checkpoint = (
            await session.execute(
                sa.select(DocumentStage).where(
                    DocumentStage.document_id == document_id,
                    DocumentStage.stage == IngestStage.EXTRACT_DOMAIN,
                )
            )
        ).scalar_one()
        attempts_before = checkpoint.attempt
        checkpoint.status = StageStatus.RUNNING  # simulate a killed worker
        await session.commit()

    await run_stage(document_id, IngestStage.EXTRACT_DOMAIN, context=context)
    assert await counts() == baseline

    async with factory() as session:
        checkpoint = (
            await session.execute(
                sa.select(DocumentStage).where(
                    DocumentStage.document_id == document_id,
                    DocumentStage.stage == IngestStage.EXTRACT_DOMAIN,
                )
            )
        ).scalar_one()
        assert checkpoint.status is StageStatus.DONE
        assert checkpoint.attempt == attempts_before + 1

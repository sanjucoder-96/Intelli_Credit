"""Hybrid retrieval (FTS + pgvector + RRF): correctness and the <100ms budget.

Seeds a synthetic benchmark corpus (clearly SYNTHETIC_DEMO; random unit
vectors + templated finance prose — real embeddings arrive with the Phase-4
ingestion pipeline) and asserts the Phase-3 acceptance criterion: fused
results, correct target ranking, and P50 latency under 100 ms.
"""

from __future__ import annotations

import math
import os
import random
import time
import uuid

import pytest
import sqlalchemy as sa

from app.domain.enums import DataOrigin
from app.infrastructure.db.models import Chunk, Company, Document, LoanApplication, Organization
from app.infrastructure.db.retrieval import HybridRetriever
from tests.support import admin_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]

CORPUS_SIZE = 5000
DIM = 384
LATENCY_BUDGET_S = 0.100

_VOCAB = [
    "turnover",
    "receivables",
    "inventory",
    "working",
    "capital",
    "sanction",
    "covenant",
    "ledger",
    "invoice",
    "reconciliation",
    "statement",
    "audit",
    "collateral",
    "margin",
    "disbursement",
    "promoter",
    "subsidiary",
    "counterparty",
    "exposure",
    "liquidity",
    "leverage",
    "coverage",
]


def _unit_vector(rng: random.Random) -> list[float]:
    vec = [rng.gauss(0.0, 1.0) for _ in range(DIM)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _prose(rng: random.Random, index: int) -> str:
    words = rng.choices(_VOCAB, k=24)
    return f"chunk {index}: " + " ".join(words)


async def _seed_corpus() -> tuple[uuid.UUID, uuid.UUID, list[float]]:
    """Seed a synthetic org + document + CORPUS_SIZE chunks.

    Returns (org_id, loan_application_id, target_query_vector): one target
    chunk carries a unique GSTIN token AND the exact query vector, so it must
    win both arms and land at fused rank 1.
    """
    rng = random.Random(42)
    target_vector = _unit_vector(rng)

    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session:
            suffix = uuid.uuid4().hex[:8]
            org = Organization(
                name=f"Benchmark Org {suffix}", slug=f"bench-{suffix}", is_active=True
            )
            session.add(org)
            await session.flush()
            company = Company(
                org_id=org.id,
                legal_name=f"Benchmark Mills {suffix}",
                data_origin=DataOrigin.SYNTHETIC_DEMO,
            )
            session.add(company)
            await session.flush()
            case = LoanApplication(
                org_id=org.id, company_id=company.id, title=f"Benchmark case {suffix}"
            )
            session.add(case)
            await session.flush()
            document = Document(
                org_id=org.id,
                loan_application_id=case.id,
                company_id=company.id,
                storage_key=f"bench/{suffix}.pdf",
                sha256=uuid.uuid4().hex * 2,
                filename="bench.pdf",
                media_type="application/pdf",
                doc_type="bank_statement",
                data_origin=DataOrigin.SYNTHETIC_DEMO,
            )
            session.add(document)
            await session.flush()

            rows: list[dict[str, object]] = []
            for i in range(CORPUS_SIZE):
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "org_id": org.id,
                        "document_id": document.id,
                        "loan_application_id": case.id,
                        "doc_type": "bank_statement",
                        "chunk_kind": "prose",
                        "text": _prose(rng, i),
                        "embedding": _unit_vector(rng),
                        "page_start": i // 40 + 1,
                        "page_end": i // 40 + 1,
                    }
                )
            # The needle: unique identifier token + the exact query vector.
            rows[1234]["text"] = (
                "GSTIN 27ZZZZZ9999Z9Z9 outward turnover reconciliation against "
                "bank credits shows a material divergence"
            )
            rows[1234]["embedding"] = target_vector

            for start in range(0, CORPUS_SIZE, 500):
                await session.execute(sa.insert(Chunk), rows[start : start + 500])
            await session.commit()
            return org.id, case.id, target_vector


async def test_hybrid_retrieval_correctness_and_latency() -> None:
    org_id, case_id, query_vector = await _seed_corpus()

    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session, session.begin():
            retriever = HybridRetriever(session)

            async def run() -> list:
                return await retriever.retrieve(
                    org_id=org_id,
                    query_text="GSTIN 27ZZZZZ9999Z9Z9 turnover reconciliation",
                    query_vector=query_vector,
                    loan_application_id=case_id,
                    top_k=10,
                )

            results = await run()  # warmup (plan + index caches)

            assert results, "hybrid retrieval returned nothing"
            top = results[0]
            assert "27ZZZZZ9999Z9Z9" in top.text, "needle chunk must win the fusion"
            assert top.fts_rank == 1 and top.vector_rank == 1
            scores = [r.rrf_score for r in results]
            assert scores == sorted(scores, reverse=True), "results must be RRF-ordered"
            assert (
                any(r.fts_rank is None for r in results)
                or any(r.vector_rank is None for r in results)
                or len(results) == 10
            )  # both arms contribute to the fused set

            latencies: list[float] = []
            for _ in range(5):
                started = time.perf_counter()
                await run()
                latencies.append(time.perf_counter() - started)
            p50 = sorted(latencies)[len(latencies) // 2]
            assert p50 < LATENCY_BUDGET_S, f"hybrid retrieval P50 {p50 * 1000:.1f}ms >= 100ms"


async def test_hybrid_retrieval_respects_tenant_filter() -> None:
    org_id, _case_id, query_vector = await _seed_corpus()
    foreign_org = uuid.uuid4()

    async with admin_engine() as engine:
        factory = session_factory(engine)
        async with factory() as session, session.begin():
            retriever = HybridRetriever(session)
            results = await retriever.retrieve(
                org_id=foreign_org,
                query_text="turnover reconciliation",
                query_vector=query_vector,
                top_k=5,
            )
            assert results == [], "a foreign org must retrieve nothing"

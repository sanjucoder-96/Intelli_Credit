"""Hybrid retrieval: Postgres FTS + pgvector fused with RRF in one query.

The ADR-0002 mechanism: both arms run as CTEs over the same ``chunks`` table
with identical filters (tenant, case, doc types, fiscal period), each ranked
independently, then fused with Reciprocal Rank Fusion (k = 60). Keyword
search is non-negotiable in finance (exact GSTIN/CIN/invoice identifiers);
the vector arm carries semantic recall. ``hnsw.iterative_scan`` is enabled
per transaction (pgvector >= 0.8) so selective case filters don't starve the
ANN arm. Phase 4 wires this behind the ``SemanticMemory`` protocol for
agents; Phase 5 adds the feature-flagged reranker on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

RRF_K = 60
ARM_LIMIT = 50


@dataclass(frozen=True)
class RetrievedChunk:
    """One fused retrieval hit with both arms' diagnostics."""

    chunk_id: UUID
    document_id: UUID
    text: str
    doc_type: str
    chunk_kind: str
    page_start: int | None
    page_end: int | None
    rrf_score: float
    fts_rank: int | None
    vector_rank: int | None


_HYBRID_SQL = sa.text(
    f"""
    WITH fts AS (
        SELECT id,
               row_number() OVER (
                   ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('english', :query_text)) DESC
               ) AS rank
        FROM chunks
        WHERE org_id = :org_id
          AND (CAST(:loan_application_id AS uuid) IS NULL
               OR loan_application_id = :loan_application_id)
          AND (CAST(:doc_types AS text[]) IS NULL
               OR doc_type = ANY(CAST(:doc_types AS text[])))
          AND (CAST(:fiscal_period AS text) IS NULL OR fiscal_period = :fiscal_period)
          AND tsv @@ websearch_to_tsquery('english', :query_text)
        LIMIT {ARM_LIMIT}
    ),
    vec AS (
        SELECT id,
               row_number() OVER (
                   ORDER BY embedding <=> CAST(:query_vector AS vector)
               ) AS rank
        FROM chunks
        WHERE org_id = :org_id
          AND (CAST(:loan_application_id AS uuid) IS NULL
               OR loan_application_id = :loan_application_id)
          AND (CAST(:doc_types AS text[]) IS NULL
               OR doc_type = ANY(CAST(:doc_types AS text[])))
          AND (CAST(:fiscal_period AS text) IS NULL OR fiscal_period = :fiscal_period)
        ORDER BY embedding <=> CAST(:query_vector AS vector)
        LIMIT {ARM_LIMIT}
    ),
    fused AS (
        SELECT COALESCE(fts.id, vec.id)                          AS id,
               COALESCE(1.0 / ({RRF_K} + fts.rank), 0)
             + COALESCE(1.0 / ({RRF_K} + vec.rank), 0)           AS rrf_score,
               fts.rank                                          AS fts_rank,
               vec.rank                                          AS vector_rank
        FROM fts
        FULL OUTER JOIN vec ON fts.id = vec.id
    )
    SELECT c.id, c.document_id, c.text, c.doc_type, c.chunk_kind,
           c.page_start, c.page_end,
           fused.rrf_score, fused.fts_rank, fused.vector_rank
    FROM fused
    JOIN chunks c ON c.id = fused.id
    ORDER BY fused.rrf_score DESC, c.id
    LIMIT :top_k
    """
)


class HybridRetriever:
    """Session-bound hybrid retrieval over the ``chunks`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def retrieve(
        self,
        *,
        org_id: UUID,
        query_text: str,
        query_vector: list[float],
        loan_application_id: UUID | None = None,
        doc_types: list[str] | None = None,
        fiscal_period: str | None = None,
        top_k: int = 10,
    ) -> list[RetrievedChunk]:
        """Run both arms and return the RRF-fused top-k.

        The tenant filter is explicit here *and* enforced by RLS when the
        session comes from :func:`~app.infrastructure.db.engine.tenant_session`
        — two layers, per ADR-0013.
        """
        # pgvector >= 0.8: relax HNSW ordering so post-filtered scans keep
        # producing candidates instead of starving under selective filters.
        await self._session.execute(sa.text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))

        result = await self._session.execute(
            _HYBRID_SQL,
            {
                # UUIDs pass as-is (asyncpg maps uuid.UUID natively; strings
                # would infer as text and break the uuid comparison).
                "org_id": org_id,
                "query_text": query_text,
                "query_vector": str(query_vector),
                "loan_application_id": loan_application_id,
                "doc_types": doc_types,
                "fiscal_period": fiscal_period,
                "top_k": top_k,
            },
        )
        rows: list[Any] = list(result)
        return [
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                text=row.text,
                doc_type=row.doc_type,
                chunk_kind=row.chunk_kind,
                page_start=row.page_start,
                page_end=row.page_end,
                rrf_score=float(row.rrf_score),
                fts_rank=row.fts_rank,
                vector_rank=row.vector_rank,
            )
            for row in rows
        ]

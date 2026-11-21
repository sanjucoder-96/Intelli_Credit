"""DocumentTypeRegistry — the taxonomy and per-type processing spec (ADR-0008).

One of the nine extension-surface registries: a future vertical adds document
types by registering specs, never by forking the pipeline. Tier-1
classification is deterministic regex anchoring (Indian statutory forms are
templated — free and covers most volume); tier-2 is Gemini Flash constrained
to this taxonomy (pipeline stage, Phase 4). Extraction strategies:

- ``deterministic`` — templated forms/spreadsheets parsed in code
  (GSTR-3B/2B, bank statements);
- ``llm`` — narrative documents (financial statements, ITR, CIBIL, rating
  reports) bound to a schema via Gemini; parks without an API key;
- ``none`` — kept as searchable evidence only (chunked + embedded, no typed
  extraction): contracts, legal notices, board minutes, DD notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from app.docintel.schemas import FinancialStatementExtract, ItrExtract


class ExtractionStrategy(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    NONE = "none"


class ChunkingProfile(StrEnum):
    PROSE = "prose"  # heading-anchored recursive split
    STATEMENT = "statement"  # row-group chunks, headers repeated
    KV = "kv"  # short form documents: page-sized chunks


@dataclass(frozen=True)
class DocTypeSpec:
    """Processing contract for one document type."""

    doc_type: str
    display_name: str
    classifier_anchors: tuple[str, ...] = ()
    extraction: ExtractionStrategy = ExtractionStrategy.NONE
    llm_schema: type[BaseModel] | None = None
    llm_instruction: str = ""
    chunking: ChunkingProfile = ChunkingProfile.PROSE
    table_bearing: bool = False

    def anchor_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(anchor, re.IGNORECASE) for anchor in self.classifier_anchors)


class DocumentTypeRegistry:
    """Spec lookup + tier-1 anchor classification."""

    def __init__(self) -> None:
        self._specs: dict[str, DocTypeSpec] = {}

    def register(self, spec: DocTypeSpec) -> DocTypeSpec:
        self._specs[spec.doc_type] = spec
        return spec

    def get(self, doc_type: str) -> DocTypeSpec:
        return self._specs.get(doc_type, self._specs["unclassified"])

    def types(self) -> list[str]:
        return sorted(self._specs)

    def classify_tier1(self, first_pages_text: str) -> tuple[str, float] | None:
        """Anchor-regex classification over the leading pages' text.

        Returns (doc_type, confidence) on an anchor hit; None sends the
        document to tier-2 (Gemini) or ``unclassified``.
        """
        for spec in self._specs.values():
            for pattern in spec.anchor_patterns():
                if pattern.search(first_pages_text):
                    return spec.doc_type, 0.95
        return None


document_type_registry = DocumentTypeRegistry()
"""Process-wide registry; per-workflow enablement joins in via DB (Phase 5)."""


_SPECS: tuple[DocTypeSpec, ...] = (
    DocTypeSpec(
        doc_type="gstr3b",
        display_name="GSTR-3B monthly return",
        classifier_anchors=(r"FORM\s+GSTR-?3B", r"GSTR-?3B"),
        extraction=ExtractionStrategy.DETERMINISTIC,
        chunking=ChunkingProfile.KV,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="gstr2b",
        display_name="GSTR-2B ITC statement",
        classifier_anchors=(r"FORM\s+GSTR-?2B", r"GSTR-?2B"),
        extraction=ExtractionStrategy.DETERMINISTIC,
        chunking=ChunkingProfile.STATEMENT,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="gstr2a",
        display_name="GSTR-2A statement",
        classifier_anchors=(r"FORM\s+GSTR-?2A", r"GSTR-?2A"),
        extraction=ExtractionStrategy.DETERMINISTIC,  # same shape as 2B
        chunking=ChunkingProfile.STATEMENT,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="gstr1",
        display_name="GSTR-1 outward supplies",
        classifier_anchors=(r"FORM\s+GSTR-?1\b",),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.STATEMENT,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="bank_statement",
        display_name="Bank statement",
        classifier_anchors=(
            r"statement\s+of\s+account",
            r"account\s+statement",
            r"\bIFSC\b.*\b(savings|current)\b",
        ),
        extraction=ExtractionStrategy.DETERMINISTIC,
        chunking=ChunkingProfile.STATEMENT,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="itr",
        display_name="Income-tax return",
        classifier_anchors=(r"INDIAN\s+INCOME\s*TAX\s+RETURN", r"\bITR-?[1-7V]\b", r"ITR\s+ACK"),
        extraction=ExtractionStrategy.LLM,
        llm_schema=ItrExtract,
        llm_instruction=(
            "Extract the PAN, assessment year, gross total income, total income, "
            "total tax paid, and business turnover (if stated) from this Indian "
            "income-tax return document. Use null for anything not present."
        ),
        chunking=ChunkingProfile.KV,
    ),
    DocTypeSpec(
        doc_type="balance_sheet",
        display_name="Balance sheet",
        classifier_anchors=(r"balance\s+sheet\s+as\s+at",),
        extraction=ExtractionStrategy.LLM,
        llm_schema=FinancialStatementExtract,
        llm_instruction=(
            "Extract canonical line items per fiscal year from this balance sheet. "
            "Report amounts exactly as stated (do not convert units); null for absent items."
        ),
        chunking=ChunkingProfile.STATEMENT,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="profit_loss",
        display_name="Profit & loss statement",
        classifier_anchors=(r"(statement\s+of\s+)?profit\s+(and|&)\s+loss",),
        extraction=ExtractionStrategy.LLM,
        llm_schema=FinancialStatementExtract,
        llm_instruction=(
            "Extract canonical line items per fiscal year from this profit-and-loss "
            "statement. Null for absent items."
        ),
        chunking=ChunkingProfile.STATEMENT,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="cash_flow",
        display_name="Cash-flow statement",
        classifier_anchors=(r"cash\s+flow\s+statement", r"statement\s+of\s+cash\s+flows"),
        extraction=ExtractionStrategy.LLM,
        llm_schema=FinancialStatementExtract,
        llm_instruction="Extract cash-flow line items per fiscal year; null for absent items.",
        chunking=ChunkingProfile.STATEMENT,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="annual_report",
        display_name="Annual report",
        classifier_anchors=(r"annual\s+report\s+20\d{2}",),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.PROSE,
    ),
    DocTypeSpec(
        doc_type="cibil_commercial",
        display_name="CIBIL commercial report",
        classifier_anchors=(r"CIBIL", r"commercial\s+credit\s+report", r"\bCMR\b"),
        extraction=ExtractionStrategy.NONE,  # typed CIBIL parse lands in Phase 5
        chunking=ChunkingProfile.KV,
    ),
    DocTypeSpec(
        doc_type="sanction_letter",
        display_name="Sanction letter",
        classifier_anchors=(r"sanction\s+letter", r"credit\s+facility.*sanction"),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.PROSE,
    ),
    DocTypeSpec(
        doc_type="legal_notice",
        display_name="Legal notice",
        classifier_anchors=(r"legal\s+notice", r"notice\s+under\s+section"),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.PROSE,
    ),
    DocTypeSpec(
        doc_type="board_minutes",
        display_name="Board meeting minutes",
        classifier_anchors=(r"minutes\s+of\s+the\s+(board|meeting)",),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.PROSE,
    ),
    DocTypeSpec(
        doc_type="shareholding_pattern",
        display_name="Shareholding pattern",
        classifier_anchors=(r"shareholding\s+pattern",),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.STATEMENT,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="rating_report",
        display_name="Rating agency report",
        classifier_anchors=(r"(CRISIL|ICRA|CARE|India\s+Ratings).*(rating|rationale)",),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.PROSE,
    ),
    DocTypeSpec(
        doc_type="invoice",
        display_name="Invoice",
        classifier_anchors=(r"tax\s+invoice",),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.KV,
        table_bearing=True,
    ),
    DocTypeSpec(
        doc_type="contract_mou",
        display_name="Contract / MoU",
        classifier_anchors=(r"memorandum\s+of\s+understanding", r"this\s+agreement\s+is\s+made"),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.PROSE,
    ),
    DocTypeSpec(
        doc_type="dd_note",
        display_name="Due-diligence note",
        classifier_anchors=(),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.PROSE,
    ),
    DocTypeSpec(
        doc_type="unclassified",
        display_name="Unclassified",
        classifier_anchors=(),
        extraction=ExtractionStrategy.NONE,
        chunking=ChunkingProfile.PROSE,
    ),
)

for _spec in _SPECS:
    document_type_registry.register(_spec)

TAXONOMY: tuple[str, ...] = tuple(document_type_registry.types())
"""All registered document types (the tier-2 classifier's enum)."""

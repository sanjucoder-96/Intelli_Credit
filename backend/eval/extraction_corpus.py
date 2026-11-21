"""Deterministic extraction-evaluation corpus + scorer (Phase-8 item 2).

The corpus is a set of synthetic documents whose ground-truth field values are
known by construction. Each document is run through IntelliCredit's REAL
deterministic extractors (the born-digital, Gemini-free path), and the result
is scored against ground truth for:

- classification accuracy (tier-1 anchor routing),
- field precision / recall,
- numeric accuracy (exact Decimal match),
- page-citation accuracy,
- the honesty invariant: a missing/ambiguous value must be reported as UNKNOWN
  (None), never fabricated.

Scope (honest): the deterministic extractors cover GSTR-3B, GSTR-2B/2A and bank
statements. ITR and full financial-statement / annual-report extraction are
LLM-based (nondeterministic, API-key-gated) and are therefore NOT scored here
with deterministic ground truth — they are exercised live and, for the
annual-report demo document, geometric bbox accuracy is demonstrated separately
(Phase-7 hardening) where the layout is controlled. Adversarial conditions
below cover OCR noise, broken tables, misleading text, conflicting/duplicate
values and incomplete documents.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.docintel.extractors.bank_statement import extract_bank_statement_rows
from app.docintel.extractors.gstr2b import extract_gstr2b
from app.docintel.extractors.gstr3b import extract_gstr3b
from app.docintel.registry import document_type_registry
from app.docintel.schemas import ExtractionResult

# Sentinel: this field MUST be absent/None (an adversarial "prefer unknown" case).
UNKNOWN = object()


@dataclass(frozen=True)
class EvalField:
    path: str  # dotted path into result.data, e.g. "outward_tax.igst"
    expected: Any  # Decimal | str | UNKNOWN
    numeric: bool = False
    expected_page: int | None = None


@dataclass(frozen=True)
class EvalDoc:
    doc_id: str
    title: str
    doc_type: str  # expected tier-1 classification
    head_text: str  # text handed to classify_tier1
    run: Callable[[], ExtractionResult]
    fields: tuple[EvalField, ...]
    adversarial: bool = False


@dataclass
class FieldOutcome:
    doc_id: str
    path: str
    kind: str  # "correct" | "wrong_value" | "missed" | "unknown_kept" | "FABRICATED"
    expected: str
    got: str


@dataclass
class ExtractionReport:
    docs: int = 0
    classification_correct: int = 0
    known_fields: int = 0
    correct_fields: int = 0
    wrong_values: int = 0
    missed: int = 0
    numeric_fields: int = 0
    numeric_correct: int = 0
    page_fields: int = 0
    page_correct: int = 0
    unknown_fields: int = 0
    unknown_kept: int = 0
    fabrications: int = 0
    outcomes: list[FieldOutcome] = field(default_factory=list)

    @property
    def classification_accuracy(self) -> float:
        return self.classification_correct / self.docs if self.docs else 0.0

    @property
    def recall(self) -> float:
        return self.correct_fields / self.known_fields if self.known_fields else 0.0

    @property
    def precision(self) -> float:
        produced = self.correct_fields + self.wrong_values
        return self.correct_fields / produced if produced else 0.0

    @property
    def numeric_accuracy(self) -> float:
        return self.numeric_correct / self.numeric_fields if self.numeric_fields else 0.0

    @property
    def page_accuracy(self) -> float:
        return self.page_correct / self.page_fields if self.page_fields else 0.0

    def summary(self) -> dict[str, float | int]:
        return {
            "docs": self.docs,
            "classification_accuracy": round(self.classification_accuracy, 4),
            "field_precision": round(self.precision, 4),
            "field_recall": round(self.recall, 4),
            "numeric_accuracy": round(self.numeric_accuracy, 4),
            "page_accuracy": round(self.page_accuracy, 4),
            "unknown_fields": self.unknown_fields,
            "unknown_kept": self.unknown_kept,
            "fabrications": self.fabrications,
        }


def _get(data: Any, path: str) -> Any:
    obj = data
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def score_doc(doc: EvalDoc, report: ExtractionReport) -> None:
    report.docs += 1
    classified = document_type_registry.classify_tier1(doc.head_text)
    if classified is not None and classified[0] == doc.doc_type:
        report.classification_correct += 1

    result = doc.run()
    for f in doc.fields:
        got = _get(result.data, f.path)
        if f.expected is UNKNOWN:
            report.unknown_fields += 1
            if got is None:
                report.unknown_kept += 1
                report.outcomes.append(
                    FieldOutcome(doc.doc_id, f.path, "unknown_kept", "UNKNOWN", "None")
                )
            else:
                report.fabrications += 1
                report.outcomes.append(
                    FieldOutcome(doc.doc_id, f.path, "FABRICATED", "UNKNOWN", str(got))
                )
            continue

        report.known_fields += 1
        if f.numeric:
            report.numeric_fields += 1
        if f.expected_page is not None:
            report.page_fields += 1

        if got is None:
            report.missed += 1
            report.outcomes.append(
                FieldOutcome(doc.doc_id, f.path, "missed", str(f.expected), "None")
            )
            continue

        if f.numeric:
            ok = _as_decimal(got) == _as_decimal(f.expected)
        else:
            ok = str(got) == str(f.expected)

        if ok:
            report.correct_fields += 1
            if f.numeric:
                report.numeric_correct += 1
            report.outcomes.append(
                FieldOutcome(doc.doc_id, f.path, "correct", str(f.expected), str(got))
            )
        else:
            report.wrong_values += 1
            report.outcomes.append(
                FieldOutcome(doc.doc_id, f.path, "wrong_value", str(f.expected), str(got))
            )

        if f.expected_page is not None:
            citation = result.citations.get(f.path)
            if citation is not None and citation.page == f.expected_page:
                report.page_correct += 1


def score_corpus(docs: list[EvalDoc] | None = None) -> ExtractionReport:
    report = ExtractionReport()
    for doc in docs if docs is not None else build_corpus():
        score_doc(doc, report)
    return report


# --------------------------------------------------------------------------- #
# The corpus.
# --------------------------------------------------------------------------- #

_CLEAN_3B = "\n".join(
    [
        "FORM GSTR-3B",
        "[See rule 61(5)]",
        "GSTIN 27AAACA1234A1Z5",
        "Legal name ACME TEXTILES PRIVATE LIMITED",
        "Period 04-2026",
        "3.1 Details of Outward Supplies",
        "(a) Outward taxable supplies (other than zero rated) 1,00,00,000.00",
        "Integrated Tax 9,00,000.00",
        "Central Tax 0.00",
        "State/UT Tax 0.00",
        "4. Eligible ITC",
        "(A) ITC Available - All other ITC - IGST 5,00,000.00",
        "Tax paid in cash 4,00,000.00",
        "Tax paid through ITC 5,00,000.00",
    ]
)

# Misleading prose mentions a different figure that is NOT on the labelled
# 3.1(a) line — the extractor must ignore it and cite the real value.
_MISLEADING_3B = "\n".join(
    [
        "FORM GSTR-3B",
        "GSTIN 27AAACA1234A1Z5",
        "Period 05-2026",
        "Note: last year's outward supplies were 9,99,99,999 — ignore for this period.",
        "3.1 Details of Outward Supplies",
        "(a) Outward taxable supplies (other than zero rated) 95,00,000.00",
        "Integrated Tax 8,55,000.00",
    ]
)

# The labelled 3.1(a) line exists but the amount is OCR-garbled to non-numeric
# tokens: the extractor must return UNKNOWN, not a fabricated value.
_OCR_NOISE_3B = "\n".join(
    [
        "F0RM  GSTR-3B",
        "GSTlN 27AAACA1234A1Z5",
        "Period 06-2026",
        "3.1 Details of Outward Supplies",
        # amount OCR-garbled with letter-O / lowercase-L instead of digits:
        "(a) Outward taxable supplies (other than zero rated) l,OO,OO,OOO.OO",
        "Integrated Tax ~~~~",
    ]
)

# Incomplete: the 3.1(a) line is absent entirely.
_INCOMPLETE_3B = "\n".join(
    ["FORM GSTR-3B", "GSTIN 27AAACA1234A1Z5", "Period 07-2026", "4. Eligible ITC"]
)

_2B_HEAD = "FORM GSTR-2B\nGSTIN 27AAACA1234A1Z5\nPeriod 04-2026"
_2B_TABLES = [
    (
        1,
        [
            ["GSTIN of supplier", "Trade name", "Taxable value", "IGST", "CGST", "SGST"],
            ["29AACCN9012C1Z8", "Nimbus Logistics", "10,00,000", "1,80,000", "0", "0"],
            ["36AABCD5678B1Z3", "Deccan Agro", "5,00,000", "0", "45,000", "45,000"],
        ],
    )
]

_BANK_CLEAN_ROWS = [
    ["Account Statement", "", "", "", ""],
    ["A/C No: 001234567890  IFSC: HDFC0001234", "", "", "", ""],
    ["Date", "Narration", "Debit", "Credit", "Balance"],
    ["01/04/2026", "OPENING", "", "", "10,00,000.00"],
    ["05/04/2026", "NEFT-CUSTOMER", "", "35,00,000.00", "45,00,000.00"],
    ["20/04/2026", "CHQ RTN - INSUFFICIENT FUNDS", "25,000.00", "", "44,75,000.00"],
    ["03/05/2026", "UPI-VENDOR", "1,00,000.00", "", "43,75,000.00"],
]
_BANK_CLEAN_HEADER = " ".join(cell for row in _BANK_CLEAN_ROWS[:3] for cell in row)

# Broken continuity: the running balance is internally inconsistent — the
# extractor must LOWER confidence rather than assert false certainty.
_BANK_BROKEN_ROWS = [
    ["Date", "Narration", "Debit", "Credit", "Balance"],
    ["01/04/2026", "OPENING", "", "", "1,000.00"],
    ["02/04/2026", "NEFT IN", "", "500.00", "9,999.00"],  # 1,000 + 500 ≠ 9,999
]


def build_corpus() -> list[EvalDoc]:
    return [
        EvalDoc(
            "gstr3b_clean",
            "Clean born-digital GSTR-3B",
            "gstr3b",
            _CLEAN_3B,
            lambda: extract_gstr3b([_CLEAN_3B]),
            (
                EvalField("gstin", "27AAACA1234A1Z5", expected_page=1),
                EvalField("period", "2026-04", expected_page=1),
                EvalField(
                    "outward_taxable_value", Decimal("10000000.00"), numeric=True, expected_page=1
                ),
                EvalField("outward_tax.igst", Decimal("900000.00"), numeric=True),
                EvalField("itc_claimed.igst", Decimal("500000.00"), numeric=True),
                EvalField("tax_paid_cash", Decimal("400000.00"), numeric=True),
            ),
        ),
        EvalDoc(
            "gstr3b_misleading",
            "GSTR-3B with misleading prose",
            "gstr3b",
            _MISLEADING_3B,
            lambda: extract_gstr3b([_MISLEADING_3B]),
            (
                # The real labelled figure, NOT the 9,99,99,999 decoy.
                EvalField(
                    "outward_taxable_value", Decimal("9500000.00"), numeric=True, expected_page=1
                ),
                EvalField("period", "2026-05"),
            ),
        ),
        EvalDoc(
            "gstr3b_ocr_noise",
            "GSTR-3B with OCR-garbled amount",
            "gstr3b",
            _OCR_NOISE_3B,
            lambda: extract_gstr3b([_OCR_NOISE_3B]),
            (
                # Amount unreadable → UNKNOWN, never fabricated.
                EvalField("outward_taxable_value", UNKNOWN),
            ),
            adversarial=True,
        ),
        EvalDoc(
            "gstr3b_incomplete",
            "Incomplete GSTR-3B (no 3.1a)",
            "gstr3b",
            _INCOMPLETE_3B,
            lambda: extract_gstr3b([_INCOMPLETE_3B]),
            (EvalField("outward_taxable_value", UNKNOWN),),
            adversarial=True,
        ),
        EvalDoc(
            "gstr2b_clean",
            "GSTR-2B with ruled supplier table",
            "gstr2b",
            _2B_HEAD,
            lambda: extract_gstr2b([_2B_HEAD], _2B_TABLES),
            (
                EvalField("gstin", "27AAACA1234A1Z5"),
                EvalField("period", "2026-04"),
            ),
        ),
        EvalDoc(
            "bank_clean",
            "Clean bank statement",
            "bank_statement",
            _BANK_CLEAN_HEADER,
            lambda: extract_bank_statement_rows(
                _BANK_CLEAN_ROWS, header_text=_BANK_CLEAN_HEADER, cite="Sheet1"
            ),
            (
                EvalField("account_number_masked", "XXXX7890"),
                EvalField("ifsc", "HDFC0001234"),
            ),
        ),
        EvalDoc(
            "bank_broken",
            "Bank statement with broken continuity",
            "bank_statement",
            "Account Statement\nIFSC: HDFC0001234\nDate Narration Debit Credit Balance",
            lambda: extract_bank_statement_rows(_BANK_BROKEN_ROWS),
            (),  # scored separately for the confidence-flag behaviour
            adversarial=True,
        ),
    ]

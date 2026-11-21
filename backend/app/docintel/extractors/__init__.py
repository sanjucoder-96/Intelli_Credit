"""Per-document-type extractors.

Deterministic extractors (templated statutory forms, spreadsheets) parse
without any model; LLM-backed extraction (narrative statements) binds a
schema to Gemini and honestly parks when no API key is configured. Each
extractor computes field confidence from deterministic post-validation —
never from self-grading.
"""

from app.docintel.extractors.bank_statement import (
    extract_bank_statement_rows,
    extract_bank_statement_xlsx,
)
from app.docintel.extractors.gstr2b import extract_gstr2b
from app.docintel.extractors.gstr3b import extract_gstr3b

__all__ = [
    "extract_bank_statement_rows",
    "extract_bank_statement_xlsx",
    "extract_gstr2b",
    "extract_gstr3b",
]

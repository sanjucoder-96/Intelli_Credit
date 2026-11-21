"""Shared parsing helpers for Indian financial documents.

Pure functions only (no I/O): amount normalization for Indian digit grouping
(``1,23,45,678.90``), parenthesized negatives, currency markers; tolerant
date parsing for the formats that dominate statements and statutory forms;
and labeled-amount lookup over text lines.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_AMOUNT_CLEAN = re.compile(r"[₹\s,]|Rs\.?|INR", re.IGNORECASE)
_AMOUNT_SHAPE = re.compile(r"^\(?-?\d+(\.\d+)?\)?$")

_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y-%m-%d",
)

MONTH_PERIOD = re.compile(r"(0[1-9]|1[0-2])[-/ ]?(20\d{2})")
GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b")
PAN_PATTERN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def parse_amount(raw: str) -> Decimal | None:
    """Parse an Indian-format amount; parentheses mean negative.

    Returns None for text that is not an amount — callers must treat that as
    "missing", never as zero.
    """
    cleaned = _AMOUNT_CLEAN.sub("", raw).strip()
    if not cleaned or not _AMOUNT_SHAPE.match(cleaned):
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(raw: str) -> date | None:
    """Parse the date formats common on Indian statements; None if unparseable."""
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_period(text: str) -> str | None:
    """Extract an MM-YYYY period token (GSTR headers) as ``YYYY-MM``."""
    match = MONTH_PERIOD.search(text)
    if match is None:
        return None
    month, year = match.group(1), match.group(2)
    return f"{year}-{month}"


def find_labeled_amount(lines: list[str], label_pattern: str) -> tuple[Decimal, int, str] | None:
    """Find the first amount on a line matching ``label_pattern``.

    Returns (amount, line_index, matched_line). The amount is the LAST
    numeric token on the line — statutory forms put the value in the final
    column. Case-insensitive.
    """
    compiled = re.compile(label_pattern, re.IGNORECASE)
    for index, line in enumerate(lines):
        if compiled.search(line):
            tokens = re.findall(r"\(?-?[\d,]+(?:\.\d+)?\)?", line)
            for token in reversed(tokens):
                amount = parse_amount(token)
                if amount is not None:
                    return amount, index, line
    return None


def close_enough(a: Decimal, b: Decimal, *, tolerance: Decimal = Decimal("1.0")) -> bool:
    """Whether two amounts agree within a rounding tolerance (₹1 default)."""
    return abs(a - b) <= tolerance

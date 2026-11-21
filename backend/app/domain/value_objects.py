"""Self-validating Indian corporate identifier types (ARCHITECTURE §3).

Each identifier is an ``Annotated[str, AfterValidator(...)]`` type usable in
any pydantic model, plus a standalone ``validate_*`` function for non-pydantic
call sites (e.g. extraction validators in Phase 4). Values are normalized to
uppercase with surrounding whitespace stripped before validation.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

_CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def _normalize(value: str) -> str:
    return value.strip().upper()


def validate_cin(value: str) -> str:
    """Validate a Corporate Identification Number.

    Format per the Ministry of Corporate Affairs (Companies Act 2013):
    listing status (L/U) + 5-digit industry code + 2-letter state code +
    4-digit incorporation year + 3-letter ownership type + 6-digit
    registration number, 21 characters total.
    """
    normalized = _normalize(value)
    if not _CIN_RE.fullmatch(normalized):
        raise ValueError(f"Invalid CIN: {value!r}")
    return normalized


def validate_gstin(value: str) -> str:
    """Validate a Goods and Services Tax Identification Number.

    Format per the GSTN specification: 2-digit state code + 10-character PAN +
    entity code + 'Z' literal at position 14 + checksum character, 15
    characters total.
    """
    normalized = _normalize(value)
    if not _GSTIN_RE.fullmatch(normalized):
        raise ValueError(f"Invalid GSTIN: {value!r}")
    return normalized


def validate_pan(value: str) -> str:
    """Validate a Permanent Account Number.

    Format per the Income Tax Department: 5 letters + 4 digits + 1 letter,
    10 characters total (4th letter encodes holder type, e.g. 'C' company).
    """
    normalized = _normalize(value)
    if not _PAN_RE.fullmatch(normalized):
        raise ValueError(f"Invalid PAN: {value!r}")
    return normalized


def validate_ifsc(value: str) -> str:
    """Validate an Indian Financial System Code.

    Format per RBI: 4-letter bank code + '0' reserved literal + 6-character
    alphanumeric branch code, 11 characters total.
    """
    normalized = _normalize(value)
    if not _IFSC_RE.fullmatch(normalized):
        raise ValueError(f"Invalid IFSC: {value!r}")
    return normalized


CIN = Annotated[str, AfterValidator(validate_cin)]
GSTIN = Annotated[str, AfterValidator(validate_gstin)]
PAN = Annotated[str, AfterValidator(validate_pan)]
IFSC = Annotated[str, AfterValidator(validate_ifsc)]

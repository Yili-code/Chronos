"""Secret-free evidence helpers for repeated PDF download checks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum


PDF_SIGNATURE = b"%PDF-"
_VERSION_RE = re.compile(rb"%PDF-(\d\.\d)")


class PdfEvidenceStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class PdfSample:
    status: PdfEvidenceStatus
    byte_count: int
    sha256: str
    pdf_version: str | None
    error: str | None = None


@dataclass(frozen=True)
class PdfRepeatability:
    status: str
    samples: tuple[PdfSample, ...]
    reason: str


def inspect_pdf_bytes(data: bytes) -> PdfSample:
    """Inspect bytes without retaining or logging their contents."""
    digest = hashlib.sha256(data).hexdigest()
    version_match = _VERSION_RE.match(data[:16])
    version = version_match.group(1).decode("ascii") if version_match else None
    if not data.startswith(PDF_SIGNATURE):
        return PdfSample(
            status=PdfEvidenceStatus.INVALID,
            byte_count=len(data),
            sha256=digest,
            pdf_version=version,
            error="missing_pdf_signature",
        )
    if len(data) < 32 or b"%%EOF" not in data[-1024:]:
        return PdfSample(
            status=PdfEvidenceStatus.INVALID,
            byte_count=len(data),
            sha256=digest,
            pdf_version=version,
            error="missing_pdf_eof",
        )
    return PdfSample(
        status=PdfEvidenceStatus.VALID,
        byte_count=len(data),
        sha256=digest,
        pdf_version=version,
    )


def assess_repeatability(samples: list[PdfSample] | tuple[PdfSample, ...]) -> PdfRepeatability:
    """Return a conservative result for repeated downloads of one attachment."""
    normalized = tuple(samples)
    if len(normalized) < 2:
        return PdfRepeatability("insufficient_evidence", normalized, "at_least_two_samples_required")
    if any(sample.status is not PdfEvidenceStatus.VALID for sample in normalized):
        return PdfRepeatability("deferred_attachment", normalized, "one_or_more_invalid_samples")
    if len({sample.sha256 for sample in normalized}) != 1:
        return PdfRepeatability("deferred_attachment", normalized, "checksum_mismatch")
    if len({sample.byte_count for sample in normalized}) != 1:
        return PdfRepeatability("deferred_attachment", normalized, "size_mismatch")
    return PdfRepeatability("verified", normalized, "matching_valid_samples")

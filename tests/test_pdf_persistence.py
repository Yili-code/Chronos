from chronos.pdf_persistence import (
    PdfEvidenceStatus,
    assess_repeatability,
    inspect_pdf_bytes,
)


PDF = b"%PDF-1.7\n" + b"x" * 40 + b"\n%%EOF\n"


def test_inspection_records_safe_integrity_fields_only():
    sample = inspect_pdf_bytes(PDF)

    assert sample.status is PdfEvidenceStatus.VALID
    assert sample.byte_count == len(PDF)
    assert sample.pdf_version == "1.7"
    assert len(sample.sha256) == 64
    assert not hasattr(sample, "body")


def test_repeatability_requires_two_matching_valid_samples():
    samples = [inspect_pdf_bytes(PDF), inspect_pdf_bytes(PDF)]

    result = assess_repeatability(samples)

    assert result.status == "verified"
    assert result.reason == "matching_valid_samples"


def test_invalid_or_mismatched_samples_are_deferred():
    invalid = inspect_pdf_bytes(b"not a pdf")
    mismatch = inspect_pdf_bytes(PDF + b"different")

    assert assess_repeatability([invalid, invalid]).status == "deferred_attachment"
    assert assess_repeatability([inspect_pdf_bytes(PDF), mismatch]).status == "deferred_attachment"

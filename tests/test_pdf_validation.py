from io import BytesIO
import pytest
from pypdf import PdfWriter
from chronos.pdf_validation import pdf_page_count
from chronos.pdf_validation import isolated_pdf_page_count


def make_pdf(*, encrypted=False, pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("synthetic-test-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_reads_actual_page_tree():
    assert pdf_page_count(make_pdf()) == 2


def test_isolated_worker_reads_real_pdf():
    assert isolated_pdf_page_count(make_pdf()) == 2


def test_worker_timeout_and_secret_environment(monkeypatch):
    import subprocess
    from chronos import pdf_validation
    monkeypatch.setenv("CHRONOS_GEMINI_API_KEY", "synthetic-secret")
    def timeout(command, **options):
        assert "CHRONOS_GEMINI_API_KEY" not in options["env"]
        assert "-I" in command
        assert options["stderr"] == subprocess.DEVNULL
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(pdf_validation.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="pdf_parser_failed_or_timed_out"):
        isolated_pdf_page_count(make_pdf())


@pytest.mark.parametrize("data", [b"%PDF-1.7\n" + b"x" * 40 + b"\n%%EOF", make_pdf(encrypted=True), make_pdf(pages=0)])
def test_envelope_alone_encryption_or_empty_tree_is_not_valid(data):
    with pytest.raises(ValueError, match="pdf_unreadable_or_unsupported"):
        pdf_page_count(data)

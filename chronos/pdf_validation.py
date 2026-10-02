"""Structural PDF checks; not semantic accuracy or visual-layout validation."""
from io import BytesIO
import logging
from pypdf import PdfReader
from .pdf_persistence import inspect_pdf_bytes, PdfEvidenceStatus
import os
from pathlib import Path
import subprocess
import sys

# Parser diagnostics can include untrusted document strings. Return only codes.
logging.getLogger("pypdf").disabled = True
logging.getLogger("pypdf").addHandler(logging.NullHandler())
logging.getLogger("pypdf").propagate = False
logging.getLogger("pypdf._reader").disabled = True


def pdf_page_count(data: bytes) -> int:
    if not isinstance(data, bytes) or not 32 <= len(data) <= 12 * 1024 * 1024:
        raise ValueError("pdf_size_invalid")
    if inspect_pdf_bytes(data).status is not PdfEvidenceStatus.VALID:
        raise ValueError("pdf_envelope_invalid")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted")
        count = len(reader.pages)
        if not 1 <= count <= 1000:
            raise ValueError("page_limit")
        for page in reader.pages:
            if len(page.mediabox) != 4:
                raise ValueError("invalid_page")
        return count
    except Exception:
        raise ValueError("pdf_unreadable_or_unsupported") from None


def isolated_pdf_page_count(data: bytes, *, timeout: float = 10) -> int:
    if not isinstance(data, bytes) or not 32 <= len(data) <= 12 * 1024 * 1024:
        raise ValueError("pdf_size_invalid")
    if inspect_pdf_bytes(data).status is not PdfEvidenceStatus.VALID:
        raise ValueError("pdf_envelope_invalid")
    if not 0 < timeout <= 30:
        raise ValueError("invalid_parser_timeout")
    # No shell, no inherited provider keys, no document-controlled arguments.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("pdf_worker.py"))],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0 or not result.stdout.strip().isdigit():
            raise ValueError()
        count = int(result.stdout.strip())
        if not 1 <= count <= 1000:
            raise ValueError()
        return count
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError("pdf_parser_failed_or_timed_out") from None

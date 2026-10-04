"""Structural PDF checks; not semantic accuracy or visual-layout validation."""
from io import BytesIO
import logging
import json
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


def isolated_pdf_page_count(data: bytes, *, timeout: float = 10, page_range=None, extract_text=False):
    if not isinstance(data, bytes) or not 32 <= len(data) <= 12 * 1024 * 1024:
        raise ValueError("pdf_size_invalid")
    if inspect_pdf_bytes(data).status is not PdfEvidenceStatus.VALID:
        raise ValueError("pdf_envelope_invalid")
    if not 0 < timeout <= 30:
        raise ValueError("invalid_parser_timeout")
    # No shell, no inherited provider keys, no document-controlled arguments.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    args = []
    if extract_text:
        if page_range is not None:
            raise ValueError('text extraction requires an already sliced PDF')
        args = ['--text']
    if page_range is not None:
        start, end = page_range
        if any(type(p) is not int for p in page_range) or not 1 <= start <= end <= 1000 or end - start > 3:
            raise ValueError("invalid page range")
        args = [str(start), str(end)]
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("pdf_worker.py")), *args],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if extract_text:
            texts = json.loads(result.stdout)
            if result.returncode != 0 or not isinstance(texts, list) or not 1 <= len(texts) <= 4 or any(not isinstance(t, str) for t in texts) or sum(map(len, texts)) > 100000:
                raise ValueError()
            return texts
        if page_range is not None:
            if result.returncode != 0 or inspect_pdf_bytes(result.stdout).status is not PdfEvidenceStatus.VALID:
                raise ValueError()
            return result.stdout
        if result.returncode != 0 or not result.stdout.strip().isdigit():
            raise ValueError()
        count = int(result.stdout.strip())
        if not 1 <= count <= 1000:
            raise ValueError()
        return count
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError("pdf_parser_failed_or_timed_out") from None

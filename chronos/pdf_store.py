"""Bounded content-addressed local PDF storage; no authentication material."""
import base64
import binascii
import os
from pathlib import Path
import tempfile

from .pdf_persistence import inspect_pdf_bytes, PdfEvidenceStatus

MAX_PDF_BYTES = 12 * 1024 * 1024


class PdfStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()

    def accept(self, payload: dict, catalog: list[dict]) -> dict:
        if set(payload) != {"status", "source_id", "byte_count", "data_base64"}:
            raise ValueError("unexpected PDF fields")
        if payload["status"] != "downloaded" or not isinstance(payload["source_id"], str):
            raise ValueError("invalid download status")
        if not any(row["source_id"] == payload["source_id"] for row in catalog):
            raise ValueError("unknown PDF source")
        size = payload["byte_count"]
        encoded = payload["data_base64"]
        if type(size) is not int or not 32 <= size <= MAX_PDF_BYTES:
            raise ValueError("invalid PDF size")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_PDF_BYTES + 2) // 3):
            raise ValueError("invalid encoded PDF size")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("invalid PDF encoding") from None
        evidence = inspect_pdf_bytes(data)
        if len(data) != size or evidence.status is not PdfEvidenceStatus.VALID:
            raise ValueError("invalid PDF envelope")
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{evidence.sha256}.pdf"
        # Atomic replace never exposes an incomplete file to a concurrent reader.
        fd, temporary = tempfile.mkstemp(prefix="incoming-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"status": "persisted", "source_id": payload["source_id"],
                "sha256": evidence.sha256, "byte_count": size}

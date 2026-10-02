"""Bounded content-addressed local PDF storage; no authentication material."""
import base64
import binascii
import os
from pathlib import Path
import tempfile
import sqlite3
import re
from datetime import datetime, timezone
from .material_bridge import validate_material_observation

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
        validate_material_observation({"status": "observed", "materials": catalog})
        matches = [row for row in catalog if row["source_id"] == payload["source_id"]]
        if len(matches) != 1:
            raise ValueError("unknown PDF source")
        metadata = matches[0]
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
        # File first, index second: interrupted indexing leaves an orphan blob,
        # never an index pointing at a partially written download.
        with sqlite3.connect(self.directory / "index.sqlite3") as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS pdf_sources (course_id TEXT, source_id TEXT, filename TEXT, activity_id TEXT, sha256 TEXT, byte_count INTEGER, saved_at TEXT, PRIMARY KEY(course_id, source_id))")
            connection.execute("INSERT INTO pdf_sources VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(course_id, source_id) DO UPDATE SET filename=excluded.filename, activity_id=excluded.activity_id, sha256=excluded.sha256, byte_count=excluded.byte_count, saved_at=excluded.saved_at",
                (metadata["course_id"], metadata["source_id"], metadata["filename"], metadata["activity_id"], evidence.sha256, size, datetime.now(timezone.utc).isoformat()))
        return {"status": "persisted", "source_id": payload["source_id"],
                "sha256": evidence.sha256, "byte_count": size}

    def catalog(self, course_id: str) -> list[dict]:
        index = self.directory / "index.sqlite3"
        if not index.exists():
            return []
        with sqlite3.connect(index) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM pdf_sources WHERE course_id=? ORDER BY source_id", (course_id,)).fetchall()
        return [dict(row) for row in rows]

    def read(self, course_id: str, source_id: str, *, expected_sha256: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("invalid PDF identity")
        matches = [row for row in self.catalog(course_id) if row["source_id"] == source_id]
        if len(matches) != 1 or matches[0]["sha256"] != expected_sha256:
            raise ValueError("PDF source changed or unavailable")
        try:
            with (self.directory / f"{expected_sha256}.pdf").open("rb") as stream:
                data = stream.read(MAX_PDF_BYTES + 1)
        except OSError:
            raise ValueError("PDF file unavailable") from None
        evidence = inspect_pdf_bytes(data)
        if len(data) != matches[0]["byte_count"] or evidence.sha256 != expected_sha256 or evidence.status is not PdfEvidenceStatus.VALID:
            raise ValueError("PDF integrity check failed")
        return data

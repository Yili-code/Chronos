"""Import only randomly named Chronos downloads from one configured directory."""
import base64
from pathlib import Path
import re
from .pdf_store import MAX_PDF_BYTES


def import_native_pdf(payload, root: Path | None, store, catalog):
    if root is None or set(payload) != {"course_id", "source_id", "basename", "byte_count"}:
        raise ValueError("native import unavailable")
    name = payload["basename"]
    size = payload["byte_count"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-f0-9]{32}\.pdf", name):
        raise ValueError("invalid import name")
    if type(size) is not int or not 32 <= size <= MAX_PDF_BYTES:
        raise ValueError("invalid import size")
    if not isinstance(payload["source_id"], str) or not any(row["source_id"] == payload["source_id"] for row in catalog):
        raise ValueError("unknown source")
    directory = root.resolve(strict=True)
    candidate = directory / name
    if candidate.is_symlink() or candidate.resolve(strict=True).parent != directory:
        raise ValueError("invalid import path")
    with candidate.open("rb") as stream:
        data = stream.read(MAX_PDF_BYTES + 1)
    if len(data) != size:
        raise ValueError("import size mismatch")
    return store.accept({"status":"downloaded", "source_id":payload["source_id"],
        "byte_count":size, "data_base64":base64.b64encode(data).decode("ascii")}, catalog)

import base64
import pytest
from chronos.pdf_store import PdfStore

CATALOG = [{"source_id": "1", "course_id": "2", "activity_id": "3", "filename": "lecture.pdf", "uploaded_at": None}]


def payload():
    data = b"%PDF-1.7\n" + b"x" * 40 + b"\n%%EOF"
    return {"status": "downloaded", "source_id": "1", "byte_count": len(data),
            "data_base64": base64.b64encode(data).decode()}


def test_repeated_store_returns_same_content_identity(tmp_path):
    store = PdfStore(tmp_path)
    first = store.accept(payload(), CATALOG)
    assert store.accept(payload(), CATALOG) == first
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    assert (tmp_path / f"{first['sha256']}.pdf").read_bytes() == base64.b64decode(payload()["data_base64"])


@pytest.mark.parametrize("change", [{"source_id": "../../escape"}, {"byte_count": True},
    {"byte_count": 100}, {"data_base64": "bad"}, {"cookies": "rejected"}])
def test_invalid_input_leaves_no_files(tmp_path, change):
    with pytest.raises(ValueError):
        PdfStore(tmp_path).accept({**payload(), **change}, CATALOG)
    assert list(tmp_path.iterdir()) == []


def test_index_survives_restart_and_rejects_corrupt_or_wrong_course(tmp_path):
    receipt = PdfStore(tmp_path).accept(payload(), CATALOG)
    store = PdfStore(tmp_path)
    assert store.catalog("2")[0]["filename"] == "lecture.pdf"
    assert store.catalog("other") == []
    assert store.read("2", "1", expected_sha256=receipt["sha256"]) == base64.b64decode(payload()["data_base64"])
    with pytest.raises(ValueError):
        store.read("other", "1", expected_sha256=receipt["sha256"])
    (tmp_path / f"{receipt['sha256']}.pdf").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        store.read("2", "1", expected_sha256=receipt["sha256"])

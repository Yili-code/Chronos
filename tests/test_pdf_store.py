import base64
import pytest
from chronos.pdf_store import PdfStore


def payload():
    data = b"%PDF-1.7\n" + b"x" * 40 + b"\n%%EOF"
    return {"status": "downloaded", "source_id": "1", "byte_count": len(data),
            "data_base64": base64.b64encode(data).decode()}


def test_repeated_store_returns_same_content_identity(tmp_path):
    store = PdfStore(tmp_path)
    first = store.accept(payload(), [{"source_id": "1"}])
    assert store.accept(payload(), [{"source_id": "1"}]) == first
    assert len(list(tmp_path.iterdir())) == 1
    assert (tmp_path / f"{first['sha256']}.pdf").read_bytes() == base64.b64decode(payload()["data_base64"])


@pytest.mark.parametrize("change", [{"source_id": "../../escape"}, {"byte_count": True},
    {"byte_count": 100}, {"data_base64": "bad"}, {"cookies": "rejected"}])
def test_invalid_input_leaves_no_files(tmp_path, change):
    with pytest.raises(ValueError):
        PdfStore(tmp_path).accept({**payload(), **change}, [{"source_id": "1"}])
    assert list(tmp_path.iterdir()) == []

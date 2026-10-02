from datetime import datetime, timezone
import pytest
from chronos.note_record import NoteRecord
from test_firestore import database


def note(**changes):
    return NoteRecord.model_validate({
        "content_fingerprint": "a" * 64, "course_id": "1", "course": "OS",
        "class_date": "2026-10-02", "reported_progress": "process",
        "sources": [{"source_id": "2", "filename": "lecture.pdf", "sha256": "b" * 64, "page_count": 3}],
        "markdown": "# Process\n\n程序與執行緒。", "model": "configured-model",
        "prompt_version": "v1", "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc), **changes})


def test_firestore_canonical_retry_does_not_overwrite(monkeypatch):
    db = database(monkeypatch)
    original = note()
    assert db.save_study_note(original) == original
    assert db.save_study_note(note(markdown="different retry")) == original
    assert db.get_study_note("a" * 64) == original
    assert len(db.list_study_notes()) == 1
    assert db.list_study_notes("other") == []
    assert db.get_study_note("../bad") is None


def test_export_preserves_canonical_markdown():
    value = note()
    filename, data = value.export()
    assert filename == "note-" + "a" * 64 + ".md"
    assert data.decode("utf-8") == value.markdown


@pytest.mark.parametrize("changes", [{"created_at": datetime(2026, 10, 2)},
    {"generation_status": "failed"}, {"markdown": " "}, {"sources": []}])
def test_incomplete_records_are_not_canonical_notes(changes):
    with pytest.raises(ValueError):
        note(**changes)

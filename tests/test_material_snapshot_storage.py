import pytest
from chronos.material_bridge import MaterialObservationStore


def payload():
    return {"status": "observed", "materials": [{"course_id": "1", "activity_id": "2",
        "source_id": "3", "filename": "lecture.pdf", "uploaded_at": None}]}


def test_activity_snapshot_shared_across_instances(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    writer, reader = MaterialObservationStore(path), MaterialObservationStore(path)
    assert reader.course_materials("1") == []
    writer.put(payload())
    assert reader.course_materials("1") == payload()["materials"]
    snapshot = reader.course_snapshots("1")[0]
    assert snapshot["complete_course"] is False
    assert snapshot["observed_at"]
    assert snapshot["materials"][0]["uploaded_at"] is None
    writer.put({"status": "unknown", "materials": []})
    assert reader.course_materials("1") == payload()["materials"]
    assert reader.course_materials("99") == []
    with pytest.raises(ValueError):
        writer.put({**payload(), "cookie": "rejected"})
    assert reader.course_materials("1") == payload()["materials"]

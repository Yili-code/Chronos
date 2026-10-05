from copy import deepcopy
from datetime import datetime
import pytest
from chronos.assignment_bridge import validate_assignment_observation
from chronos.assignment_bridge import AssignmentObservationStore
from chronos.course_tracking import TAIPEI

NOW = datetime(2026, 10, 5, 12, tzinfo=TAIPEI)
PAYLOAD = {"status": "observed", "assignment": {
    "course_id": "123", "source_id": "456", "title": "Synthetic homework", "description": "Instructions",
    "deadline": "2026-10-07T23:59:00+08:00", "submission_status": "submitted", "attachments_status": "not_observed"}}

def validate(payload):
    return validate_assignment_observation(payload, tracked_courses={"123"}, observed_at=NOW)

def test_preserve_evidence_without_creating_tasks():
    result = validate(PAYLOAD)
    assert result["assignment"].deadline.hour == 23
    assert result["submission_status"] == "submitted"
    assert result["attachments_status"] == "not_observed"

@pytest.mark.parametrize("status", ["unknown", "unsupported_page"])
def test_unavailable_is_not_an_empty_success(status):
    assert validate({"status": status, "assignment": None})["status"] == status

@pytest.mark.parametrize("key,value", [
    ("course_id", "999"), ("source_id", "https://example.com"), ("deadline", "2026-02-30T12:00:00+08:00"),
    ("deadline", "2026-10-07"), ("submission_status", "unsubmitted"), ("attachments_status", "downloaded"),
    ("title", ""), ("description", "x" * 20001)])
def test_invalid_fields_fail_closed(key, value):
    payload = deepcopy(PAYLOAD)
    payload["assignment"][key] = value
    with pytest.raises(ValueError): validate(payload)

def test_extra_secret_fields_are_rejected():
    payload = deepcopy(PAYLOAD)
    payload["assignment"]["cookie"] = "synthetic"
    with pytest.raises(ValueError, match="unexpected assignment fields"): validate(payload)

def test_absent_deadline_remains_pending():
    payload = deepcopy(PAYLOAD)
    payload["assignment"]["deadline"] = None
    assert validate(payload)["assignment"].status == "deadline_pending"


def test_attachment_metadata_without_download_credentials(tmp_path):
    payload = deepcopy(PAYLOAD)
    payload["assignment"].update(attachments_status="observed_partial", attachments=[{"source_id": "987", "filename": "lab.pdf"}])
    store = AssignmentObservationStore(tmp_path / "data.sqlite3", {"123"})
    store.put(payload, NOW)
    assert store.snapshots()[0]["attachments"] == [{"source_id": "987", "filename": "lab.pdf"}]
    payload["assignment"]["attachments"][0]["url"] = "https://example.com/private"
    with pytest.raises(ValueError, match="unexpected attachment fields"):
        validate(payload)


def test_attachment_status_cannot_claim_complete_or_saved():
    for status in ("downloaded", "complete", "observed_partial"):
        payload = deepcopy(PAYLOAD)
        payload["assignment"]["attachments_status"] = status
        with pytest.raises(ValueError):
            validate(payload)


def test_observation_persists_without_creating_task_and_reopens(tmp_path):
    path = tmp_path / "observations.sqlite3"
    store = AssignmentObservationStore(path, {"123"})
    assert store.put(PAYLOAD, NOW)["saved"]
    assert store.put(PAYLOAD, NOW)["saved"]
    saved, = AssignmentObservationStore(path, {"123"}).snapshots()
    assert saved["submission_status"] == "submitted"
    assert saved["observed_at"] == NOW.isoformat()
    assert store.put({"status": "unknown", "assignment": None}, NOW)["saved"] is False
    assert len(store.snapshots()) == 1  # failed reads never erase evidence


def test_http_assignment_handoff_checks_origin_and_persists(tmp_path):
    import json
    import threading
    from http.client import HTTPConnection
    from chronos.local_observation_server import LocalObservationServer
    server = LocalObservationServer(0, catalog_path=tmp_path / "catalog.sqlite3", extension_id="a" * 32)
    payload = deepcopy(PAYLOAD)
    payload["assignment"]["course_id"] = "188571"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for origin, expected in [("https://example.com", 403), ("chrome-extension://" + "a" * 32, 202)]:
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request("POST", "/v1/browser-assignment", json.dumps(payload),
                               {"Content-Type": "application/json", "X-Chronos-Bridge": "1", "Origin": origin})
            response = connection.getresponse()
            assert response.status == expected
            receipt = json.loads(response.read())
            if expected == 202:
                assert receipt == {"status": "observed", "saved": True}
            connection.close()
        assert len(server.assignment_store.snapshots()) == 1
    finally:
        server.shutdown()
        server.server_close()

from copy import deepcopy
from datetime import datetime
import pytest
from chronos.assignment_bridge import validate_assignment_observation
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

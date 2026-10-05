"""Strict homework metadata handoff; browser uncertainty is never empty success."""
from datetime import datetime
import re

from .assignments import Assignment, aware


def validate_assignment_observation(payload: dict, *, tracked_courses: set[str], observed_at: datetime):
    observed_at = aware(observed_at)
    if not isinstance(payload, dict) or set(payload) != {"status", "assignment"}:
        raise ValueError("unexpected observation fields")
    status = payload["status"]
    if status not in ("observed", "unknown", "unsupported_page"):
        raise ValueError("invalid observation status")
    row = payload["assignment"]
    if status != "observed":
        if row is not None:
            raise ValueError("inconsistent observation")
        return {"status": status, "assignment": None}
    fields = {"course_id", "source_id", "title", "description", "deadline", "submission_status", "attachments_status"}
    if not isinstance(row, dict) or set(row) != fields:
        raise ValueError("unexpected assignment fields")
    for key in ("course_id", "source_id"):
        if not isinstance(row[key], str) or not re.fullmatch(r"[0-9]{1,20}", row[key]):
            raise ValueError("invalid source identity")
    if row["course_id"] not in tracked_courses:
        raise ValueError("untracked course")
    for key, limit in (("title", 500), ("description", 20000)):
        value = row[key]
        if not isinstance(value, str) or len(value) > limit or "\x00" in value:
            raise ValueError("invalid assignment text")
    if not row["title"].strip():
        raise ValueError("missing title")
    if row["submission_status"] not in ("submitted", "unknown") or row["attachments_status"] != "not_observed":
        raise ValueError("unsupported evidence state")
    deadline = row["deadline"]
    if deadline is not None:
        if not isinstance(deadline, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\+08:00", deadline):
            raise ValueError("invalid deadline format")
        deadline = datetime.fromisoformat(deadline)
    item = Assignment(row["course_id"], row["source_id"], row["title"], row["description"],
                      observed_at, deadline, "source" if deadline else None)
    return {"status": "observed", "assignment": item,
            "submission_status": row["submission_status"], "attachments_status": "not_observed"}

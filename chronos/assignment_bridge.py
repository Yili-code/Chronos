"""Strict homework metadata handoff; browser uncertainty is never empty success."""
from datetime import datetime
import re
import json
import sqlite3
from pathlib import Path

from .assignments import Assignment, aware, to_record


TRACKED_COURSES = frozenset({"189684", "189687", "189717", "192072", "189756", "188571", "193842"})


class AssignmentObservationStore:
    """Durable observations, not an assertion that a task is unsubmitted."""
    def __init__(self, path: Path, tracked_courses=TRACKED_COURSES):
        self.path = path
        self.tracked_courses = frozenset(tracked_courses)

    def put(self, payload: dict, observed_at: datetime) -> dict:
        result = validate_assignment_observation(payload, tracked_courses=self.tracked_courses, observed_at=observed_at)
        if result["status"] != "observed":
            return {"status": result["status"], "saved": False}
        item = result["assignment"]
        data = {"assignment": to_record(item), "observed_at": aware(observed_at).isoformat(),
                "submission_status": result["submission_status"], "attachments_status": result["attachments_status"]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS assignment_observations (source_key TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
            connection.execute("INSERT INTO assignment_observations VALUES (?, ?) ON CONFLICT(source_key) DO UPDATE SET record_json=excluded.record_json",
                               (item.key, json.dumps(data)))
        return {"status": "observed", "saved": True}

    def snapshots(self) -> list[dict]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path) as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT record_json FROM assignment_observations ORDER BY source_key")]


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

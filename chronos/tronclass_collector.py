"""Read-only TronClass assignment collection from authenticated JSON endpoints."""

from __future__ import annotations

from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urlencode

from .assignments import Assignment, aware
from .assignment_bridge import TRACKED_COURSES

TRACKED_SEMESTER = "1151"
from .tronclass_auth import ProbeFailure, TRONCLASS_ORIGIN, authenticated_tronclass_client


class CollectorFailure(RuntimeError):
    """A sanitized scanner failure safe to persist and notify after retry."""


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)


def _plain_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    parser = _Text()
    parser.feed(value)
    return re.sub(r"\s+", " ", unescape(" ".join(parser.parts))).strip()[:limit]


def _deadline(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise CollectorFailure("homework_deadline_shape")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CollectorFailure("homework_deadline_shape") from error
    if parsed.utcoffset() is None:
        raise CollectorFailure("homework_deadline_shape")
    return aware(parsed)


def _json_get(client, path: str) -> dict:
    try:
        response = client.get(f"{TRONCLASS_ORIGIN}{path}", headers={"Accept": "application/json"})
    except Exception as error:
        raise CollectorFailure("tronclass_api_transport") from error
    if response.status_code != 200:
        raise CollectorFailure(f"tronclass_api_status_{response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        raise CollectorFailure("tronclass_api_json") from error
    if not isinstance(payload, dict):
        raise CollectorFailure("tronclass_api_shape")
    return payload


def _configured_semester_id(client) -> int:
    semesters = _json_get(client, "/api/my-semesters").get("semesters")
    if not isinstance(semesters, list):
        raise CollectorFailure("semesters_shape")
    active = [item for item in semesters if isinstance(item, dict) and item.get("name") == TRACKED_SEMESTER]
    if len(active) != 1 or type(active[0].get("id")) is not int or active[0]["id"] <= 0:
        raise CollectorFailure("configured_semester_shape")
    return active[0]["id"]


def _paged_rows(client, path, key):
    result, seen = [], set()
    separator = "&" if "?" in path else "?"
    for page in range(1, 21):
        rows = _json_get(client, f"{path}{separator}page={page}&page_size=100").get(key)
        if not isinstance(rows, list) or len(rows) > 100:
            raise CollectorFailure(f"{key}_shape")
        for row in rows:
            if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 or row["id"] in seen:
                raise CollectorFailure(f"{key}_identity")
            seen.add(row["id"])
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise CollectorFailure(f"{key}_pagination_limit")


def collect_assignments(username: str, password: str, observed_at: datetime, *, excluded_course_ids: set[str] | None = None) -> list[Assignment]:
    """Return all current homework metadata, never submitting or marking it read."""
    excluded = excluded_course_ids or set()
    observed_at = aware(observed_at)
    result: list[Assignment] = []
    try:
        with authenticated_tronclass_client(username, password) as client:
            # The upstream is_active flag can refer to a past semester. Use the
            # semester explicitly paired with our tracked course roster.
            semester_id = _configured_semester_id(client)
            conditions = urlencode({"conditions": '{"semester_id":[' + str(semester_id) + ']}'})
            courses = _paged_rows(client, f"/api/my-courses?{conditions}", "courses")
            if not isinstance(courses, list):
                raise CollectorFailure("courses_shape")
            for course in courses:
                if not isinstance(course, dict) or type(course.get("id")) is not int or course["id"] <= 0:
                    raise CollectorFailure("courses_shape")
                course_id = str(course["id"])
                if not isinstance(course.get("semester"), dict) or type(course["semester"].get("id")) is not int or course["semester"].get("id") != semester_id:
                    raise CollectorFailure("course_semester_mismatch")
                if course_id not in TRACKED_COURSES or course_id in excluded:
                    continue
                rows = _paged_rows(client, f"/api/courses/{course_id}/homework-activities", "homework_activities")
                if not isinstance(rows, list):
                    raise CollectorFailure("homework_shape")
                for row in rows:
                    if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
                        raise CollectorFailure("homework_shape")
                    # Only explicit unsubmitted evidence can become new work.
                    if row.get("submitted") is not False:
                        continue
                    title = _plain_text(row.get("title"), limit=500)
                    if not title:
                        raise CollectorFailure("homework_title_shape")
                    description = _plain_text(row.get("description", row.get("content", "")), limit=20_000)
                    deadline = _deadline(row.get("end_time", row.get("deadline")))
                    result.append(Assignment(
                        course_id=course_id,
                        source_id=str(row["id"]),
                        title=title,
                        description=description,
                        discovered_at=observed_at,
                        deadline=deadline,
                        deadline_origin="source" if deadline else None,
                        submission_status="submitted" if row.get("submitted") is True else "unknown",
                    ))
    except ProbeFailure as error:
        raise CollectorFailure(error.stage) from None
    finally:
        password = ""
    return result

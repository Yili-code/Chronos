"""Inspect authenticated TronClass navigation without persisting user content.

This one-shot probe exists to identify the stable, read-only assignment-list
route before the scheduled collector is implemented.  It emits only HTTP
status, same-origin path shapes and numeric route identifiers; it never logs
page text, cookies, credentials, tickets or query strings.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import os
from urllib.parse import urljoin, urlsplit

from .tronclass_auth import ProbeFailure, TRONCLASS_INDEX_URL, TRONCLASS_ORIGIN, authenticated_tronclass_client
from .tronclass_collector import TRACKED_SEMESTER
from .assignment_bridge import TRACKED_COURSES


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def _safe_paths(html: str) -> list[str]:
    parser = _Links()
    parser.feed(html)
    paths = set()
    for href in parser.hrefs:
        target = urlsplit(urljoin(TRONCLASS_ORIGIN, href))
        if target.scheme != "https" or target.netloc != "tronclass.ntou.edu.tw":
            continue
        # Query strings can carry transient identifiers, so they are deliberately omitted.
        if target.path.startswith("/user/") or target.path.startswith("/course/"):
            paths.add(target.path)
    return sorted(paths)[:200]


def discover(username: str, password: str) -> dict[str, object]:
    try:
        with authenticated_tronclass_client(username, password) as client:
            response = client.get(TRONCLASS_INDEX_URL)
            if response.status_code != 200:
                return {"failure_stage": "index_fetch", "failure_status": response.status_code}
            semesters_response = client.get(f"{TRONCLASS_ORIGIN}/api/my-semesters", headers={"Accept": "application/json"})
            if semesters_response.status_code != 200:
                return {"failure_stage": "semesters_fetch", "failure_status": semesters_response.status_code}
            try:
                semesters = semesters_response.json().get("semesters")
            except (ValueError, AttributeError):
                return {"failure_stage": "semesters_shape"}
            if not isinstance(semesters, list):
                return {"failure_stage": "semesters_shape"}
            active = [item for item in semesters if isinstance(item, dict) and item.get("name") == TRACKED_SEMESTER]
            if len(active) != 1 or type(active[0].get("id")) is not int:
                return {"failure_stage": "configured_semester_shape"}
            courses_response = client.get(
                f"{TRONCLASS_ORIGIN}/api/my-courses?conditions=%7B%22semester_id%22%3A%5B{active[0]['id']}%5D%7D&page=1&page_size=100",
                headers={"Accept": "application/json"},
            )
            if courses_response.status_code != 200:
                return {"failure_stage": "courses_fetch", "failure_status": courses_response.status_code}
            try:
                courses = courses_response.json().get("courses")
            except (ValueError, AttributeError):
                return {"failure_stage": "courses_shape"}
            if not isinstance(courses, list) or not all(isinstance(course, dict) and isinstance(course.get("id"), int)
                                                   for course in courses):
                return {"failure_stage": "courses_shape"}
            if any(not isinstance(course.get("semester"), dict) or course["semester"].get("id") != active[0]['id'] for course in courses):
                return {"failure_stage": "course_semester_mismatch"}
            courses = [course for course in courses if str(course['id']) in TRACKED_COURSES]
            homework_count = 0
            if courses:
                homework_response = client.get(
                    f"{TRONCLASS_ORIGIN}/api/courses/{courses[0]['id']}/homework-activities?page=1&page_size=100",
                    headers={"Accept": "application/json"},
                )
                if homework_response.status_code != 200:
                    return {"failure_stage": "homework_fetch", "failure_status": homework_response.status_code}
                try:
                    homework = homework_response.json().get("homework_activities")
                except (ValueError, AttributeError):
                    return {"failure_stage": "homework_shape"}
                if not isinstance(homework, list):
                    return {"failure_stage": "homework_shape"}
                homework_count = len(homework)
            return {
                "authenticated": True,
                "index_status": response.status_code,
                "course_count": len(courses),
                "first_course_homework_count": homework_count,
                "same_origin_paths": _safe_paths(response.text),
            }
    except ProbeFailure as failure:
        result: dict[str, object] = {"failure_stage": failure.stage}
        if failure.status_code is not None:
            result["failure_status"] = failure.status_code
        return result
    except Exception:
        return {"failure_stage": "unexpected_discovery_error"}
    finally:
        password = ""


def main() -> int:
    username = os.environ.get("CHRONOS_TRONCLASS_USERNAME", "")
    password = os.environ.get("CHRONOS_TRONCLASS_PASSWORD", "")
    if not username or not password:
        print(json.dumps({"failure_stage": "cloud_credentials_unavailable"}))
        return 2
    report = discover(username, password)
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("authenticated") else 1


if __name__ == "__main__":
    raise SystemExit(main())

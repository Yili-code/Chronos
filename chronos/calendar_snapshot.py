"""Validated public calendar evidence; freshness is separate from parse success."""
from dataclasses import asdict
from datetime import date, datetime
from hashlib import sha256
import re

from .academic_calendar import OFFICIAL_CALENDAR_URL, CalendarEvent, parse_calendar
from .course_tracking import TAIPEI


def make_snapshot(html: str, fetched_at: datetime) -> dict:
    if fetched_at.utcoffset() is None:
        raise ValueError("calendar clock must be timezone-aware")
    events = parse_calendar(html)
    if not events:
        raise ValueError("calendar has no dated events")
    return validate_snapshot({"source_url": OFFICIAL_CALENDAR_URL,
        "fetched_at": fetched_at.astimezone(TAIPEI).isoformat(),
        "content_sha256": sha256(html.encode()).hexdigest(),
        "events": [asdict(event) for event in events]})


def validate_snapshot(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"source_url", "fetched_at", "content_sha256", "events"}:
        raise ValueError("invalid calendar snapshot")
    if value["source_url"] != OFFICIAL_CALENDAR_URL:
        raise ValueError("unsupported calendar source")
    fetched = datetime.fromisoformat(value["fetched_at"])
    if fetched.utcoffset() is None or not re.fullmatch(r"[0-9a-f]{64}", value["content_sha256"]):
        raise ValueError("invalid calendar provenance")
    if not isinstance(value["events"], list) or not 1 <= len(value["events"]) <= 1000:
        raise ValueError("invalid calendar event count")
    events = []
    for row in value["events"]:
        event = CalendarEvent(**row)
        if date.fromisoformat(event.end_date) < date.fromisoformat(event.start_date):
            raise ValueError("reversed calendar range")
        if event.classification not in {"other", "no_class", "normal_instruction", "needs_confirmation", "exam_period"}:
            raise ValueError("invalid calendar classification")
        if not isinstance(event.text, str) or not 1 <= len(event.text) <= 5000:
            raise ValueError("invalid calendar event text")
        events.append(asdict(event))
    return {**value, "events": events}


def is_current(snapshot: dict | None, now: datetime) -> bool:
    if now.utcoffset() is None:
        raise ValueError("calendar clock must be timezone-aware")
    if snapshot is None:
        return False
    value = validate_snapshot(snapshot)
    fetched = datetime.fromisoformat(value["fetched_at"])
    return fetched <= now and fetched.astimezone(TAIPEI).date() == now.astimezone(TAIPEI).date()

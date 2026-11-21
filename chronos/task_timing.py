"""Task time roles. Date-only values never imply a time of day."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = None
    scheduled: str | None = None
    event: str | None = None
    source_text: str | None = Field(default=None, max_length=2000)
    uncertain: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, max_length=2000)
    estimated_minutes: int | None = Field(default=None, strict=True, ge=1, le=10080)

    @field_validator("scheduled", "event")
    @classmethod
    def validate_temporal_value(cls, value):
        if value is None:
            return value
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Time must include a timezone")
        return parsed.isoformat()


def normalize_timing(value: dict | None, due_at=None) -> dict:
    timing = TaskTiming.model_validate(value or {}).model_dump(mode="json", exclude_none=True)
    if due_at and timing.get("due_date"):
        raise ValueError("Use either a deadline date or a deadline time, not both")
    return timing


def task_sort_key(task: dict, tz=None) -> tuple:
    deadline = task.get("due_at") or task.get("timing", {}).get("due_date")
    if not deadline:
        return True, '', 0, 0, task['id']
    if len(deadline) == 10:
        # Date-only priority precedes timed deadlines on that local day. This
        # is a sorting convention, not an invented submission time.
        return False, deadline, 0, 0, task['id']
    tz = tz or ZoneInfo('Asia/Taipei')
    instant = datetime.fromisoformat(deadline)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=tz)
    return False, instant.astimezone(tz).date().isoformat(), 1, instant.timestamp(), task['id']


def display_date(value: str) -> str:
    day = date.fromisoformat(value[:10])
    weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[day.weekday()]
    return f"{day:%m/%d} ({weekday})"


def display_time(value: str | None, tz) -> str:
    """Render dates with weekday, preserving time and timezone conversion."""
    if not value:
        return "None"
    if len(value) == 10:
        return display_date(value)
    local = datetime.fromisoformat(value).astimezone(tz)
    return display_date(local.date().isoformat()) + local.strftime(" %H:%M")


def timing_details(task: dict, tz) -> list[tuple[str, str]]:
    timing = task.get("timing") or {}
    result = []
    if timing.get("content"):
        result.append(("Content", timing["content"]))
    for label, value in (("Due", task.get("due_at") or timing.get("due_date")),
                         ("Scheduled", timing.get("scheduled")), ("Course / event", timing.get("event"))):
        if value:
            result.append((label, display_time(value, tz)))
    if timing.get("uncertain"):
        result.append(("Time needs clarification", timing["uncertain"]))
    return result


def urgent_deadline(task: dict, tz, *, now: datetime | None = None) -> bool:
    """Flag open deadlines less than three days away, including overdue ones."""
    if task.get("status") == "done" or task.get("completed_at"):
        return False
    deadline = task.get("due_at") or (task.get("timing") or {}).get("due_date")
    if not deadline:
        return False
    current = (now or datetime.now(tz)).astimezone(tz)
    if len(deadline) == 10:
        return (date.fromisoformat(deadline) - current.date()).days < 3
    due = datetime.fromisoformat(deadline)
    if due.tzinfo is None:
        due = due.replace(tzinfo=tz)
    return due.astimezone(timezone.utc) - current.astimezone(timezone.utc) < timedelta(days=3)

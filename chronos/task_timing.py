"""Task time roles. Date-only values never imply a time of day."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = None
    scheduled: str | None = None
    event: str | None = None
    source_text: str | None = Field(default=None, max_length=2000)
    uncertain: str | None = Field(default=None, max_length=2000)

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


def task_sort_key(task: dict) -> tuple:
    deadline = task.get("due_at") or task.get("timing", {}).get("due_date")
    return deadline is None, deadline or "", task["id"]


def display_time(value: str | None, tz) -> str:
    if not value:
        return "None"
    if len(value) == 10:
        return value
    return datetime.fromisoformat(value).astimezone(tz).strftime("%Y-%m-%d %H:%M")


def timing_details(task: dict, tz) -> list[tuple[str, str]]:
    timing = task.get("timing") or {}
    result = []
    for label, value in (("Due", task.get("due_at") or timing.get("due_date")),
                         ("Scheduled", timing.get("scheduled")), ("Course / event", timing.get("event"))):
        if value:
            result.append((label, display_time(value, tz)))
    if timing.get("uncertain"):
        result.append(("Time needs clarification", timing["uncertain"]))
    return result

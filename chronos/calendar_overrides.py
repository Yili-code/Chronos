"""Owner-controlled daily-scan calendar policy, separate from public evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re


DEFAULT_TERM_START = "2026-09-07"
DEFAULT_TERM_END = "2027-01-31"


@dataclass(frozen=True)
class CalendarOverrides:
    term_start: str = DEFAULT_TERM_START
    term_end: str = DEFAULT_TERM_END
    enabled_dates: tuple[str, ...] = ()
    disabled_dates: tuple[str, ...] = ()

    def __post_init__(self):
        start, end = date.fromisoformat(self.term_start), date.fromisoformat(self.term_end)
        if end < start:
            raise ValueError("term end precedes term start")
        for values in (self.enabled_dates, self.disabled_dates):
            if len(values) > 366 or tuple(sorted(set(values))) != values:
                raise ValueError("invalid calendar override dates")
            for value in values:
                date.fromisoformat(value)
        if set(self.enabled_dates) & set(self.disabled_dates):
            raise ValueError("conflicting calendar override")

    @classmethod
    def from_record(cls, value: dict | None) -> "CalendarOverrides":
        if value is None:
            return cls()
        if set(value) != {"term_start", "term_end", "enabled_dates", "disabled_dates"}:
            raise ValueError("invalid calendar override record")
        if not all(isinstance(value[key], str) for key in ("term_start", "term_end")):
            raise ValueError("invalid calendar override term")
        if not all(isinstance(value[key], list) and all(isinstance(item, str) for item in value[key])
                   for key in ("enabled_dates", "disabled_dates")):
            raise ValueError("invalid calendar override dates")
        return cls(value["term_start"], value["term_end"], tuple(value["enabled_dates"]), tuple(value["disabled_dates"]))

    def to_record(self) -> dict:
        return {"term_start": self.term_start, "term_end": self.term_end,
                "enabled_dates": list(self.enabled_dates), "disabled_dates": list(self.disabled_dates)}

    def allows(self, day: date) -> bool:
        value = day.isoformat()
        if value in self.enabled_dates:
            return True
        if value in self.disabled_dates:
            return False
        return self.term_start <= value <= self.term_end


def render_calendar_overrides(value: CalendarOverrides) -> str:
    disabled = "、".join(value.disabled_dates) or "無"
    enabled = "、".join(value.enabled_dates) or "無"
    return (
        "<b>TronClass 掃描校曆</b>\n"
        f"學期掃描：{value.term_start} 至 {value.term_end}\n"
        "時段：每天 06:00（Asia/Taipei；考試與假日照常）\n"
        f"停用日期：{disabled}\n"
        f"額外啟用：{enabled}\n\n"
        "編輯：/calendar disable YYYY-MM-DD\n"
        "啟用：/calendar enable YYYY-MM-DD\n"
        "學期：/calendar term YYYY-MM-DD YYYY-MM-DD\n"
        "還原單日：/calendar reset YYYY-MM-DD"
    )


def apply_calendar_override(value: CalendarOverrides, command: str) -> CalendarOverrides:
    """Apply one explicit owner command; unsupported wording never mutates state."""
    tokens = command.strip().split()
    if len(tokens) == 2 and tokens[0] in {"disable", "enable", "reset"}:
        action, target = tokens
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
            raise ValueError("calendar date must be YYYY-MM-DD")
        date.fromisoformat(target)
        enabled, disabled = set(value.enabled_dates), set(value.disabled_dates)
        if action == "disable":
            disabled.add(target); enabled.discard(target)
        elif action == "enable":
            enabled.add(target); disabled.discard(target)
        else:
            enabled.discard(target); disabled.discard(target)
        return CalendarOverrides(value.term_start, value.term_end, tuple(sorted(enabled)), tuple(sorted(disabled)))
    if len(tokens) == 3 and tokens[0] == "term":
        return CalendarOverrides(tokens[1], tokens[2], value.enabled_dates, value.disabled_dates)
    raise ValueError("Use /calendar, /calendar disable YYYY-MM-DD, /calendar enable YYYY-MM-DD, /calendar term START END, or /calendar reset YYYY-MM-DD")

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from html import escape
from zoneinfo import ZoneInfo

from .db import Database
from .task_timing import normalize_timing, timing_details


@dataclass
class ParsedTask:
    title: str
    due_at: datetime | None = None
    project: str | None = None
    timing: dict = field(default_factory=dict)


@dataclass
class DeterministicEdit:
    task: ParsedTask
    tag_alias: tuple[str, str] | None = None


def parse_literal_task(text: str) -> ParsedTask:
    """Explicit /add input is a title, not a natural-language date expression."""
    title = text.strip()
    tag = re.search(r"\s+#([A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)$", title)
    project = tag.group(1) if tag else None
    if tag:
        title = title[:tag.start()].strip()
    if not title or len(title) > 2000:
        raise ValueError("Use /add followed by a title of 1–2000 characters and an optional #tag.")
    return ParsedTask(title=title, project=project)


class TaskService:
    def __init__(self, db: Database, tz):
        self.db = db
        self.tz = tz

    def create(self, title: str, due_at: datetime | None = None, project: str | None = None, timing: dict | None = None) -> dict:
        return self.db.create_task(title.strip(), due_at, project, datetime.now(self.tz), normalize_timing(timing, due_at))

    def list_open(self) -> list[dict]:
        return self.db.list_open_tasks()

    def complete(self, task_id: int) -> bool:
        return self.db.complete_task(task_id, datetime.now(self.tz))

    def postpone(self, task_id: int, due_at: datetime) -> bool:
        return self.db.postpone_task(task_id, due_at)

    def edit(self, task_id: int, title: str, due_at: datetime | None, project: str | None, timing: dict | None = None) -> dict | None:
        if timing is not None:
            timing = normalize_timing(timing, due_at)
        if not self.db.edit_task(task_id, title.strip(), due_at, project, timing):
            return None
        return {
            "id": task_id,
            "title": title.strip(),
            "due_at": due_at.isoformat() if due_at else None,
            "project": project,
            **({"timing": timing} if timing is not None else {}),
        }

    def clear(self) -> int:
        return self.db.clear_tasks()

    def project_aliases(self) -> dict[str, str]:
        return self.db.list_tag_aliases()

    def save_project_alias(
        self, project: str, alias: str, existing_aliases: dict[str, str] | None = None
    ) -> None:
        self.db.save_tag_alias(project, alias, existing_aliases)

    def get_open_by_position(self, position: int) -> dict | None:
        open_tasks = self.list_open()
        if position < 1 or position > len(open_tasks):
            return None
        return open_tasks[position - 1]

    def get_open_by_id(self, task_id: int) -> dict | None:
        return next((task for task in self.list_open() if task["id"] == task_id), None)

    def complete_position(self, position: int) -> dict | None:
        task = self.get_open_by_position(position)
        if task is None or not self.complete(task["id"]):
            return None
        return task

    def reschedule_position(self, position: int, due_at: datetime) -> dict | None:
        task = self.get_open_by_position(position)
        if task is None or not self.postpone(task["id"], due_at):
            return None
        return {**task, "due_at": due_at.isoformat()}

    def parse(self, text: str, now: datetime | None = None) -> ParsedTask:
        now = now or datetime.now(self.tz)
        cleaned = re.sub(r"^(新增|加入|提醒我|記下|代辦)\s*[:：]?\s*", "", text.strip())
        project_match = re.search(r"(?:#|專案[:：])([\w.-]+)", cleaned)
        project = project_match.group(1) if project_match else None
        if project_match:
            cleaned = (cleaned[: project_match.start()] + cleaned[project_match.end() :]).strip()

        due_at = self._extract_due(cleaned, now)
        cleaned = re.sub(r"(今天|明天|後天|下週[一二三四五六日天]|週[一二三四五六日天])", "", cleaned)
        cleaned = re.sub(r"(?:早上|上午|中午|下午|晚上)?\s*\d{1,2}(?::|：)\d{2}", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。")
        if not cleaned:
            raise ValueError("Task text cannot be empty.")
        timing = {}
        if due_at and not re.search(r"\d{1,2}(?::|：)\d{2}", text):
            timing = {"due_date": due_at.date().isoformat(), "source_text": text}
            due_at = None
        return ParsedTask(title=cleaned, due_at=due_at, project=project, timing=timing)

    def _extract_due(self, text: str, now: datetime) -> datetime | None:
        date_value = None
        if "後天" in text:
            date_value = now.date() + timedelta(days=2)
        elif "明天" in text:
            date_value = now.date() + timedelta(days=1)
        elif "今天" in text:
            date_value = now.date()
        else:
            weekday = re.search(r"(下週|週)([一二三四五六日天])", text)
            if weekday:
                targets = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
                delta = (targets[weekday.group(2)] - now.weekday()) % 7
                if weekday.group(1) == "下週":
                    delta = delta + 7 if delta == 0 else delta
                elif delta == 0:
                    delta = 7
                date_value = now.date() + timedelta(days=delta)

        clock = re.search(r"(早上|上午|中午|下午|晚上)?\s*(\d{1,2})(?::|：)(\d{2})", text)
        if not date_value and not clock:
            return None
        date_value = date_value or now.date()
        hour, minute = 0, 0
        if clock:
            period, hour_text, minute_text = clock.groups()
            hour, minute = int(hour_text), int(minute_text)
            if period in {"下午", "晚上"} and hour < 12:
                hour += 12
            if period == "中午" and hour < 11:
                hour += 12
        return datetime.combine(date_value, time(hour, minute), tzinfo=self.tz)

    @staticmethod
    def serialize(parsed: ParsedTask) -> dict:
        data = asdict(parsed)
        data["due_at"] = parsed.due_at.isoformat() if parsed.due_at else None
        return data


def parse_deterministic_edit(current: dict, instruction: str, now: datetime | None = None) -> DeterministicEdit | None:
    """Resolve narrow field operations without sending private task text to an LLM."""
    text = re.sub(r"\s+", " ", instruction.strip())
    lowered = text.casefold()
    task = ParsedTask(
        title=current["title"],
        due_at=datetime.fromisoformat(current["due_at"]) if current.get("due_at") else None,
        project=current.get("project"),
        timing=dict(current.get("timing") or {}),
    )

    # A supplied replacement is already the desired text; no AI interpretation
    # is needed. Anchor the entire instruction so extra changes aren't ignored.
    replacement = re.fullmatch(
        r'(?:set the title to|translate the title to|title:)\s*"([^"\r\n]+)"'
        r'(?:\.?\s*Keep (?:other fields unchanged|the existing due date and tag)\.?)?\.?',
        instruction.strip(), re.IGNORECASE,
    )
    if replacement:
        title = replacement.group(1).strip()
        if not title or len(title) > 2000:
            raise ValueError("Task title must contain 1–2000 characters.")
        task.title = title
        return DeterministicEdit(task)

    # Handle the screenshot's correction without relying on an external model.
    correction = re.fullmatch(
        r"(?P<day>(?:星期|週|周)[一二三四五六日天]|\d{4}-\d{2}-\d{2})?\s*"
        r"(?:是)?(?:課程|上課|課堂|course|class|lecture|event)(?:的)?\s*"
        r"(?:時間|日期|time|date)\s*[,，;；]?\s*"
        r"(?:非|不是|並非|not(?:\s+(?:the|a))?)\s*(?:due(?:\s+(?:time|date))?|deadline|截止(?:時間|日期)?|期限)[。.!！]?",
        text, re.IGNORECASE,
    )
    if correction:
        now = now or datetime.now(ZoneInfo("Asia/Taipei"))
        original = current.get("due_at") or task.timing.get("due_date")
        day = correction.group("day")
        value = original[:10] if original else None
        if day and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            value = datetime.strptime(day, "%Y-%m-%d").date().isoformat()
        elif day:
            target = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}[day[-1]]
            if not value or datetime.fromisoformat(value).weekday() != target:
                value = (now.date() + timedelta(days=(target - now.weekday()) % 7)).isoformat()
        task.due_at = None
        task.timing.pop("due_date", None)
        task.timing.pop("uncertain", None)
        task.timing["source_text"] = text
        if value:
            task.timing["event"] = value
        else:
            task.timing["uncertain"] = "Course / event date not specified"
        return DeterministicEdit(task)

    remove_due = {
        "remove date", "remove the date", "remove due date", "remove the due date",
        "no date", "no due date", "移除日期", "移除期限", "不要日期", "不需要日期",
    }
    if lowered in remove_due:
        task.due_at = None
        task.timing.pop("due_date", None)
        return DeterministicEdit(task)

    remove_project = {
        "remove tag", "remove the tag", "remove project", "remove the project",
        "no tag", "no project", "移除標籤", "移除分類", "移除專案",
    }
    if lowered in remove_project:
        task.project = None
        return DeterministicEdit(task)

    alias_patterns = (
        r"(?:未來\s+)?(?P<project>[A-Za-z][A-Za-z -]*?)\s*(?:標籤|tag)\s*改用\s*#?(?P<alias>[A-Za-z][A-Za-z0-9]{0,9})(?:\s*[（(]?(?:存入記憶|remember(?: this)?)[）)]?)?",
        r"(?:remember\s+to\s+)?use\s+#?(?P<alias>[A-Za-z][A-Za-z0-9]{0,9})\s+for\s+(?P<project>[A-Za-z][A-Za-z -]*?)\s+tags?(?:\s+from\s+now\s+on)?",
    )
    for pattern in alias_patterns:
        match = re.fullmatch(pattern, text, re.IGNORECASE)
        if not match:
            continue
        project = canonical_project(match.group("project"))
        if task.project and canonical_project(task.project) != project:
            return None
        task.project = task.project or project
        return DeterministicEdit(task, (project, match.group("alias").upper()))

    implicit_alias = re.fullmatch(
        r"(?:未來\s+)?(?:標籤|tag)\s*改用\s*#?(?P<alias>[A-Za-z][A-Za-z0-9]{0,9})(?:\s*[（(]?(?:存入記憶|remember(?: this)?)[）)]?)?",
        text,
        re.IGNORECASE,
    )
    if implicit_alias and task.project:
        project = canonical_project(task.project)
        return DeterministicEdit(task, (project, implicit_alias.group("alias").upper()))
    return None


def canonical_project(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.casefold())).strip("-")


def format_tasks(tasks: list[dict], tz, project_aliases: dict[str, str] | None = None) -> str:
    if not tasks:
        return "No open tasks."
    lines = ["<b>Tasks</b>"]
    for position, task in enumerate(tasks, start=1):
        lines.append("")
        lines.extend(format_task_block(task, tz, position=position, project_aliases=project_aliases))
    return "\n".join(lines)


def format_task_block(
    task: dict,
    tz,
    *,
    position: int | None = None,
    project_aliases: dict[str, str] | None = None,
) -> list[str]:
    prefix = f"{position}. " if position is not None else ""
    lines = [f"{prefix}<b>{escape(task['title'])}</b>"]
    for label, value in timing_details(task, tz):
        lines.append(f"<b>{label}:</b> {escape(value)}")
    if task.get("project"):
        project = project_display_name(str(task["project"]), project_aliases)
        lines.append(f"<b>Tag:</b> #{escape(project)}")
    return lines


def format_task(task: dict, tz, *, html: bool = False) -> str:
    title = _format_course_task_title(task["title"], html=html)
    if title is None:
        title = escape(task["title"]) if html else task["title"]
    parts = [title]
    for label, value in timing_details(task, tz):
        parts.append(f"{label}: {escape(value) if html else value}")
    if task.get("project"):
        project = escape(str(task["project"])) if html else str(task["project"])
        parts.append(f"#{project}")
    return " ".join(parts)


def project_display_name(project: str, aliases: dict[str, str] | None = None) -> str:
    canonical = canonical_project(project)
    configured = (aliases or {}).get(canonical)
    if configured:
        return configured
    if len(project) <= 16:
        return project
    words = [word for word in re.split(r"[.\-_\s]+", project) if word]
    if len(words) < 2:
        return project
    abbreviation = "".join(word[0] for word in words).upper()
    return abbreviation if 2 <= len(abbreviation) <= 8 else project


def _format_course_task_title(title: str, *, html: bool) -> str | None:
    confirmation = re.fullmatch(
        r"確認(?P<course>.+?)(?:今日上課範圍（(?P<legacy>\d{4}-\d{2}-\d{2})）| (?P<short>\d{2}/\d{2}) 上課範圍)",
        title,
    )
    if confirmation:
        day = _short_date(confirmation.group("legacy") or confirmation.group("short"))
        course = escape(confirmation.group("course")) if html else confirmation.group("course")
        return f"確認{course} {day} 上課範圍"
    review = re.fullmatch(
        r"複習(?P<course>.+?)(?:（(?P<legacy>\d{4}-\d{2}-\d{2})）| (?P<short>\d{2}/\d{2}))[:：](?P<detail>.+)",
        title,
    )
    if not review:
        return None
    day = _short_date(review.group("legacy") or review.group("short"))
    course = escape(review.group("course")) if html else review.group("course")
    detail = escape(review.group("detail")) if html else review.group("detail")
    detail = f"<b>{detail}</b>" if html else detail
    return f"複習{course} {day}：{detail}"


def _short_date(value: str) -> str:
    return value[5:].replace("-", "/") if len(value) == 10 else value


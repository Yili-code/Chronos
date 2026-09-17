import re
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta

from .db import Database


@dataclass
class ParsedTask:
    title: str
    due_at: datetime | None = None
    project: str | None = None


class TaskService:
    def __init__(self, db: Database, tz):
        self.db = db
        self.tz = tz

    def create(self, title: str, due_at: datetime | None = None, project: str | None = None) -> dict:
        now = datetime.now(self.tz).isoformat()
        with self.db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks(title, project, due_at, created_at) VALUES (?, ?, ?, ?)",
                (title.strip(), project, due_at.isoformat() if due_at else None, now),
            )
            task_id = cursor.lastrowid
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row)

    def list_open(self) -> list[dict]:
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE status = 'open' ORDER BY due_at IS NULL, due_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def complete(self, task_id: int) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status = 'open'",
                (datetime.now(self.tz).isoformat(), task_id),
            )
        return cursor.rowcount == 1

    def postpone(self, task_id: int, due_at: datetime) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET due_at = ? WHERE id = ? AND status = 'open'",
                (due_at.isoformat(), task_id),
            )
        return cursor.rowcount == 1

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
            raise ValueError("代辦內容不可為空")
        return ParsedTask(title=cleaned, due_at=due_at, project=project)

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
        hour, minute = 9, 0
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


def format_tasks(tasks: list[dict], tz) -> str:
    if not tasks:
        return "目前沒有未完成代辦。"
    lines = ["未完成代辦："]
    for task in tasks:
        due = ""
        if task["due_at"]:
            value = datetime.fromisoformat(task["due_at"]).astimezone(tz)
            due = f"｜{value:%m/%d %H:%M}"
        project = f"｜#{task['project']}" if task["project"] else ""
        lines.append(f"{task['id']}. {task['title']}{due}{project}")
    return "\n".join(lines)


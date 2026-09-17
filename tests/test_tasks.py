from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from chronos.db import Database
from chronos.tasks import TaskService


TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 9, 14, 15, 0, tzinfo=TZ)


def service(tmp_path: Path) -> TaskService:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return TaskService(db, TZ)


def test_parse_natural_task(tmp_path):
    parsed = service(tmp_path).parse("新增 明天 17:30 完成提案 #Chronos", NOW)
    assert parsed.title == "完成提案"
    assert parsed.project == "Chronos"
    assert parsed.due_at == datetime(2026, 9, 15, 17, 30, tzinfo=TZ)


def test_create_and_complete(tmp_path):
    tasks = service(tmp_path)
    task = tasks.create("檢查 CI")
    assert len(tasks.list_open()) == 1
    assert tasks.complete(task["id"])
    assert tasks.list_open() == []


def test_parse_afternoon(tmp_path):
    parsed = service(tmp_path).parse("提醒我今天下午 3:00 開會", NOW)
    assert parsed.due_at == datetime(2026, 9, 14, 15, 0, tzinfo=TZ)


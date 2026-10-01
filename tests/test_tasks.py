from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from chronos.db import Database
from chronos.tasks import TaskService, format_tasks


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


def test_dynamic_positions_hide_permanent_ids(tmp_path):
    tasks = service(tmp_path)
    completed = tasks.create("Completed first")
    assert tasks.complete(completed["id"])
    no_due = tasks.create("No due date")
    later = tasks.create("Later", datetime(2026, 9, 20, 10, tzinfo=TZ))
    earlier = tasks.create("Earlier", datetime(2026, 9, 19, 10, tzinfo=TZ), "Chronos")

    assert [task["id"] for task in tasks.list_open()] == [earlier["id"], later["id"], no_due["id"]]
    assert format_tasks(tasks.list_open(), TZ) == (
        "Open tasks:\n"
        "1. Earlier | 09/19 10:00 | #Chronos\n"
        "2. Later | 09/20 10:00\n"
        "3. No due date"
    )
    assert tasks.complete_position(1)["id"] == earlier["id"]
    assert tasks.reschedule_position(2, datetime(2026, 9, 18, 8, tzinfo=TZ))["id"] == no_due["id"]
    assert tasks.get_open_by_position(0) is None
    assert tasks.get_open_by_position(3) is None


def test_edit_updates_all_fields_and_requires_open_task(tmp_path):
    tasks = service(tmp_path)
    original = tasks.create("Old title", datetime(2026, 9, 20, 10, tzinfo=TZ), "Chronos")
    edited = tasks.edit(original["id"], "New title", None, None)
    assert edited == {"id": original["id"], "title": "New title", "due_at": None, "project": None}
    assert tasks.list_open()[0]["title"] == "New title"
    assert tasks.complete(original["id"])
    assert tasks.edit(original["id"], "Should not change", None, None) is None


def test_clear_deletes_open_and_completed_tasks(tmp_path):
    tasks = service(tmp_path)
    completed = tasks.create("Completed")
    tasks.create("Open")
    assert tasks.complete(completed["id"])
    assert tasks.clear() == 2
    assert tasks.list_open() == []
    assert tasks.clear() == 0


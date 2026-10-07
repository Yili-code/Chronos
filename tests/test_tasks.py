import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from chronos.db import Database
from chronos.tasks import TaskService, format_tasks, parse_deterministic_edit


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
    assert format_tasks(tasks.list_open(), TZ, now=NOW) == (
        "<b>Tasks</b>\n\n"
        "1. <b>Earlier</b>\n"
        "<b>Due:</b> 09-19 10:00\n"
        "<b>#Chronos</b>\n\n"
        "2. <b>Later</b>\n"
        "<b>Due:</b> 09-20 10:00\n\n"
        "3. <b>No due date</b>"
    )
    assert tasks.complete_position(1)["id"] == earlier["id"]
    assert tasks.reschedule_position(2, datetime(2026, 9, 18, 8, tzinfo=TZ))["id"] == no_due["id"]
    assert tasks.get_open_by_position(0) is None
    assert tasks.get_open_by_position(3) is None


def test_course_tasks_use_separate_layers_and_html_escaping():
    rendered = format_tasks([
        {"title": "確認資訊安全實務與管理今日上課範圍（2026-10-05）",
         "due_at": None, "project": "資訊安全實務與管理"},
        {"title": "複習計算機結構 10/06：Chapter 2 to around page 43 & examples",
         "due_at": None, "project": "計算機結構"},
    ], TZ)
    assert rendered == (
        "<b>Tasks</b>\n\n"
        "1. <b>確認資訊安全實務與管理今日上課範圍（10-05）</b>\n"
        "<b>#ISPM</b>\n\n"
        "2. <b>複習計算機結構 10/06：Chapter 2 to around page 43 &amp; examples</b>\n"
        "<b>#計算機結構</b>"
    )


def test_long_project_tags_use_short_display_aliases():
    rendered = format_tasks([
        {"title": "Review chapter 2", "due_at": None, "project": "computer-architecture"},
    ], TZ)
    assert "<b>#CA</b>" in rendered
    assert "computer-architecture" not in rendered


def test_deterministic_edit_handles_saved_alias_and_field_removal():
    current = {
        "title": "Review chapter 2",
        "due_at": datetime(2026, 10, 5, 9, tzinfo=TZ).isoformat(),
        "project": "computer-architecture",
    }
    alias = parse_deterministic_edit(
        current, "未來 computer architecture 標籤改用 CA (存入記憶)"
    )
    assert alias is not None
    assert alias.tag_alias == ("computer-architecture", "CA")
    assert alias.task.project == "computer-architecture"
    removed = parse_deterministic_edit(current, "remove the due date")
    assert removed is not None and removed.task.due_at is None


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


def test_sqlite_persists_pending_edit_alias_and_multi_message_progress(tmp_path):
    db = Database(tmp_path / "receipts.db")
    db.initialize()
    db.save_pending_task_edit(80, 4, 1, "rename it", NOW)
    assert db.get_pending_task_edit(80)["instruction"] == "rename it"
    db.save_tag_alias("computer-architecture", "CA")
    assert db.list_tag_aliases() == {"computer-architecture": "CA"}
    messages = [{"text": "Updated"}, {"text": "Tasks"}]
    receipt = db.process_update(81, lambda: {"messages": messages})
    assert receipt["delivered_count"] == 0
    db.mark_update_message_delivered(81, 1)
    assert db.get_update(81)["delivered"] is False
    db.mark_update_message_delivered(81, 2)
    assert db.get_update(81)["delivered"] is True
    db.delete_pending_task_edit(80)
    assert db.get_pending_task_edit(80) is None


def test_sqlite_migrates_existing_telegram_receipts(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE telegram_updates ("
            "update_id INTEGER PRIMARY KEY, reply TEXT NOT NULL, "
            "delivered INTEGER NOT NULL DEFAULT 0)"
        )
    db = Database(path)
    db.initialize()
    with db.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(telegram_updates)")}
    assert {"messages_json", "delivered_count"} <= columns


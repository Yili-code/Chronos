import asyncio
import json
import sqlite3
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from chronos import main
from chronos.ai import AIError, ExternalAI
from chronos.db import Database
from chronos.tasks import TaskService, format_tasks, parse_deterministic_edit
from chronos.task_timing import normalize_timing
from test_ai import config, mock_provider
from test_firestore import database as firestore_database


TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 7, 10, tzinfo=TZ)


@pytest.fixture(params=["sqlite", "firestore"])
def service(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        db = Database(tmp_path / "tasks.db")
        db.initialize()
    else:
        db = firestore_database(monkeypatch)
    tasks = TaskService(db, TZ)
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "tasks", tasks)
    return tasks


def test_time_roles_survive_storage_sorting_and_edits(service):
    event = service.create("Watch Saturday's lecture", timing={"event": "2026-10-10"})
    timed = service.create("Submit report", datetime(2026, 10, 9, 17, tzinfo=TZ))
    date_only = service.create("Read paper", timing={"due_date": "2026-10-08", "scheduled": "2026-10-07"})
    items = service.list_open()
    assert [x["id"] for x in items] == [date_only["id"], timed["id"], event["id"]]
    text = format_tasks(items, TZ)
    assert "<b>Due:</b> 2026-10-08\n" in text
    assert "<b>Course / event:</b> 2026-10-10" in text
    assert "09:00" not in text and "00:00" not in text
    operation = parse_deterministic_edit(items[0], "移除期限")
    service.edit(date_only["id"], operation.task.title, operation.task.due_at,
                 operation.task.project, operation.task.timing)
    stored = service.get_open_by_id(date_only["id"])
    assert stored["due_at"] is None
    assert stored["timing"] == {"scheduled": "2026-10-07"}


def test_screenshot_correction_is_deterministic_and_transactional(service, monkeypatch):
    task = service.create("Finish remote lecture", datetime(2026, 10, 10, 9, tzinfo=TZ), "CA")
    edit = AsyncMock(side_effect=AssertionError("AI must not be needed"))
    monkeypatch.setattr(main.ai, "edit", edit)
    invalid = asyncio.run(main.handle_message("/edit 星期六是課程的時間非 due time"))
    assert "Specify the task" in invalid
    action = asyncio.run(main.prepare_message(f"/edit #{task['id']} 星期六是課程的時間非 due time"))
    receipt = service.db.process_update(700, action)
    assert service.db.process_update(700, lambda: pytest.fail("duplicate mutation")) == receipt
    stored = service.get_open_by_id(task["id"])
    assert stored["due_at"] is None
    assert stored["timing"]["event"] == "2026-10-10"
    assert stored["title"] == "Finish remote lecture" and stored["project"] == "CA"
    assert "After: None" in receipt["reply"]
    assert "After: 2026-10-10" in receipt["reply"]
    edit.assert_not_awaited()


def test_message_binding_survives_reordering_and_is_chat_scoped(service):
    task = service.create("Lecture", timing={"event": "2026-10-10"})
    service.db.bind_task_message(123, 500, task["id"])
    service.create("Urgent", datetime(2026, 10, 8, 12, tzinfo=TZ))
    assert service.list_open()[0]["id"] != task["id"]
    assert service.db.task_for_message(123, 500) == task["id"]
    assert service.db.task_for_message(999, 500) is None


def test_stale_edit_cannot_overwrite_newer_changes(service):
    task = service.create("Lecture", datetime(2026, 10, 10, 9, tzinfo=TZ))
    action = asyncio.run(main.prepare_message(f"/edit #{task['id']} 移除期限"))
    service.edit(task["id"], "Updated by another request", task["due_at"] and datetime.fromisoformat(task["due_at"]), None, {})
    receipt = service.db.process_update(710, action)
    assert "changed while the edit was being prepared" in receipt["reply"]
    stored = service.get_open_by_id(task["id"])
    assert stored["title"] == "Updated by another request"
    assert stored["due_at"] == task["due_at"]


def test_postponing_date_only_deadline_clears_date_precision(service):
    task = service.create("Report", timing={"due_date": "2026-10-08", "event": "2026-10-07"})
    assert service.postpone(task["id"], datetime(2026, 10, 9, 17, tzinfo=TZ))
    stored = service.get_open_by_id(task["id"])
    assert stored["timing"] == {"event": "2026-10-07"}
    assert stored["due_at"] == "2026-10-09T17:00:00+08:00"


def test_legacy_sqlite_migration_preserves_deadline(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, project TEXT, "
                     "due_at TEXT, status TEXT, created_at TEXT, completed_at TEXT)")
        conn.execute("INSERT INTO tasks VALUES (1, 'Legacy', NULL, '2026-10-10T09:00:00+08:00', 'open', '', NULL)")
    db = Database(path)
    db.initialize()
    db.initialize()
    task = db.list_open_tasks()[0]
    assert task["due_at"] == "2026-10-10T09:00:00+08:00"
    assert task["timing"] == {}


@pytest.mark.parametrize("timing", [{"event": "not-a-date"}, {"scheduled": "2026-10-10T09:00:00"},
                                     {"due_date": "2026-02-30"}])
def test_invalid_temporal_values_are_rejected(timing):
    with pytest.raises(ValueError):
        normalize_timing(timing)


@pytest.mark.parametrize("timing", [{"due_date": "2026-10-10"}, {"event": "2026-10-10"},
                                     {"scheduled": "2026-10-10"}, {"uncertain": "星期六"}])
def test_ai_keeps_date_precision_and_time_role(monkeypatch, timing):
    def handler(request):
        payload = json.loads(request.content)
        prompt = payload["systemInstruction"]["parts"][0]["text"]
        assert "Never invent 09:00" in prompt
        assert "ambiguous" in prompt
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
            "title": "Finish remote lecture", "due_at": None, "project": None, "timing": timing,
        })}]}}]})
    mock_provider(monkeypatch, handler)
    task = asyncio.run(ExternalAI(config()).parse("finish remote lecture in 星期六", NOW))
    assert task.due_at is None
    assert task.timing == {**timing, "source_text": "finish remote lecture in 星期六"}


def test_ai_patch_preserves_unmentioned_fields(monkeypatch):
    operations = {name: {"op": "keep", "value": None} for name in
                  ("title", "due_at", "project", "due_date", "scheduled", "event", "uncertain")}
    operations.update(due_at={"op": "clear", "value": None},
                      event={"op": "set", "value": "2026-10-10"})
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={"candidates": [
        {"content": {"parts": [{"text": json.dumps(operations)}]}}]}))
    task = asyncio.run(ExternalAI(config()).edit({
        "title": "Finish remote lecture", "due_at": "2026-10-10T09:00:00+08:00", "project": "CA",
        "timing": {"scheduled": "2026-10-12"},
    }, "星期六是課程時間，不是期限", NOW))
    assert task.due_at is None and task.project == "CA"
    assert task.title == "Finish remote lecture"
    assert task.timing["event"] == "2026-10-10"
    assert task.timing["scheduled"] == "2026-10-12"


@pytest.mark.parametrize("invalid", [{"op": "set", "value": None}, {"op": "keep", "value": "Changed"},
                                      {"op": "clear", "value": None}])
def test_invalid_title_operations_fail_without_silent_changes(monkeypatch, invalid):
    operations = {name: {"op": "keep", "value": None} for name in
                  ("title", "due_at", "project", "due_date", "scheduled", "event", "uncertain")}
    operations["title"] = invalid
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={"candidates": [
        {"content": {"parts": [{"text": json.dumps(operations)}]}}]}))
    with pytest.raises(AIError, match="invalid response"):
        asyncio.run(ExternalAI(config()).edit({"title": "Original", "due_at": None, "project": None}, "rename it", NOW))


@pytest.mark.parametrize("deadline,expected", [
    ("2026-10-10T10:00:00+08:00", False),
    ("2026-10-10T09:59:59+08:00", True),
    ("2026-10-11T10:00:00+08:00", False),
    ("2026-10-07T10:00:00+08:00", True),
    ("2026-10-06T10:00:00+08:00", True),
    ("2026-10-10T02:00:00Z", False),
    ("2026-10-09", True), ("2026-10-10", False),
])
def test_urgent_deadline_boundaries(deadline, expected):
    from chronos.task_timing import urgent_deadline
    task = {"title": "Submit", "due_at": deadline} if len(deadline) > 10 else {
        "title": "Submit", "timing": {"due_date": deadline}}
    assert urgent_deadline(task, TZ, now=NOW) is expected
    assert ("🚨" in format_tasks([task], TZ, now=NOW)) is expected


def test_only_open_deadlines_get_alert():
    from chronos.task_timing import urgent_deadline
    from chronos.tasks import format_task
    for task in ({"timing": {"event": "2026-10-07", "scheduled": "2026-10-07"}},
                 {"due_at": None}, {"status": "done", "due_at": "2026-10-07"}):
        assert not urgent_deadline(task, TZ, now=NOW)
    task = {"title": "A & B", "due_at": "2026-10-08"}
    assert format_task(task, TZ, html=True, now=NOW).startswith("🚨 A &amp; B")

from datetime import datetime, timedelta
from dataclasses import replace

import pytest

from chronos.assignments import Assignment, due_reminder
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake
from chronos.assignment_commands import deadline_action


@pytest.fixture(params=["sqlite", "firestore"])
def db(request, tmp_path, monkeypatch):
    if request.param == "firestore":
        return firestore_fake(monkeypatch)
    store = Database(tmp_path / "assignments.db")
    store.initialize()
    return store


def sample():
    now = datetime(2026, 10, 5, 12, tzinfo=TAIPEI)
    return Assignment("course-1", "source-1", "Report", "Keep instructions", now,
                      now + timedelta(days=1), "source")


def test_duplicate_discovery_preserves_one_task(db):
    item = sample()
    first = db.create_assignment(item)
    assert db.create_assignment(replace(item, title="Renamed upstream")) == first
    assert len(db.list_open_tasks()) == 1
    saved = db.get_assignment(item.key)
    assert saved["assignment"] == item and saved["task_exists"]


def test_done_through_ordinary_task_api_stops_reminders_and_keeps_history(db):
    item = sample()
    task = db.create_assignment(item)
    now = item.discovered_at
    assert db.complete_task(task["task_id"], now)
    assert not db.complete_task(task["task_id"], now + timedelta(minutes=1))
    saved = db.get_assignment(item.key)["assignment"]
    assert saved.completed_at == now
    assert due_reminder(saved, now, sent_keys=set()) is None
    db.clear_tasks()
    assert db.get_assignment(item.key)["assignment"].completed_at == now
    assert db.get_assignment(item.key)["assignment"].description == item.description
    db.create_assignment(item)
    assert not db.list_open_tasks()


def test_missing_deadline_stays_pending_after_storage(db):
    item = replace(sample(), deadline=None, deadline_origin=None)
    db.create_assignment(item)
    assert db.get_assignment(item.key)["assignment"].status == "deadline_pending"
    assert db.list_open_tasks()[0]["due_at"] is None


def test_unknown_identity_returns_none(db):
    assert db.get_assignment("missing") is None


def test_deadline_command_is_atomic_and_receipt_deduplicated(db):
    item = replace(sample(), deadline=None, deadline_origin=None)
    task_id = db.create_assignment(item)["task_id"]
    action = deadline_action(db, f"deadline {task_id} 2026-10-20 23:59")
    first = db.process_update(100, action)
    assert db.process_update(100, action) == first
    saved = db.get_assignment(item.key)["assignment"]
    assert saved.deadline_origin == "owner"
    assert saved.deadline_revision == 1
    assert saved.deadline.isoformat() == db.list_open_tasks()[0]["due_at"]
    assert saved.deadline.hour == 23 and saved.deadline.minute == 59
    db.complete_task(task_id, item.discovered_at)
    assert "未變更" in deadline_action(db, f"deadline {task_id} 2026-10-21 12:00")()


@pytest.mark.parametrize("command", ["deadline 1 tomorrow", "deadline 1 2026-02-30 12:00", "deadline 1 2026-10-20", "deadline 1 2026-10-20 24:00"])
def test_invalid_deadline_does_not_mutate(db, command):
    item = sample()
    db.create_assignment(item)
    assert "用法" in deadline_action(db, command)()
    assert db.get_assignment(item.key)["assignment"] == item

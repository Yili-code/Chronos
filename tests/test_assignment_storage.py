from datetime import datetime, timedelta
from dataclasses import replace

import pytest

from chronos.assignments import Assignment, due_reminder
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake


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

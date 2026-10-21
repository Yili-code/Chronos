import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import main
from chronos.assignments import Assignment
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.task_actions import UndoConflict
from chronos.tasks import ParsedTask, TaskService
from test_firestore import database as firestore_fake
from test_system import system


NOW = datetime(2026, 10, 16, 12, tzinfo=TAIPEI)


@pytest.fixture(params=["sqlite", "firestore"])
def store(request, tmp_path, monkeypatch):
    if request.param == "firestore":
        return firestore_fake(monkeypatch)
    db = Database(tmp_path / "undo.db")
    db.initialize()
    return db


def test_undo_restores_all_time_roles_and_linked_assignment(store):
    item = Assignment("189756", "source", "Report", "Instructions", NOW, NOW + timedelta(days=2), "source")
    task_id = store.create_assignment(item)["task_id"]
    before = store.list_open_tasks()[0]
    edit = store.edit_task_with_undo(task_id, "Changed", None, "new", {"event": "2026-10-20"})
    assert store.get_assignment(item.key)["assignment"].deadline is None
    restored = store.undo_task_edit(edit["undo_token"])
    assert restored["title"] == before["title"]
    assert store.list_open_tasks()[0] == before
    assert store.get_assignment(item.key)["assignment"] == item
    with pytest.raises(UndoConflict):
        store.undo_task_edit()


def test_undo_alias_and_latest_only(store):
    task = store.create_task("First", None, "database", NOW, {})
    one = store.edit_task_with_undo(task["id"], "First", None, "database", {}, ("database", "DB"))
    assert store.list_tag_aliases() == {"database": "DB"}
    store.undo_task_edit(one["undo_token"])
    assert store.list_tag_aliases() == {}
    one = store.edit_task_with_undo(task["id"], "Second", None, "database", {})
    two = store.edit_task_with_undo(task["id"], "Third", None, "database", {})
    with pytest.raises(UndoConflict, match="expired"):
        store.undo_task_edit(one["undo_token"])
    store.undo_task_edit(two["undo_token"])
    assert store.list_open_tasks()[0]["title"] == "Second"


@pytest.mark.parametrize("change", ["edit", "done", "clear"])
def test_undo_rejects_changed_or_missing_task(store, change):
    task = store.create_task("Original", None, None, NOW, {})
    store.edit_task_with_undo(task["id"], "Changed", None, None, {})
    if change == "edit":
        store.edit_task(task["id"], "Later", None, None, {})
    elif change == "done":
        store.complete_task(task["id"], NOW)
    else:
        store.clear_tasks()
    before = store.list_open_tasks()
    with pytest.raises(UndoConflict):
        store.undo_task_edit()
    assert store.list_open_tasks() == before


def test_upstream_assignment_change_blocks_undo(store):
    item = Assignment("189756", "source", "Report", "Instructions", NOW)
    task_id = store.create_assignment(item)["task_id"]
    store.edit_task_with_undo(task_id, "Changed", None, None, {})
    store.refresh_assignment(replace(item, description="New instructions", discovered_at=NOW + timedelta(minutes=1)))
    with pytest.raises(UndoConflict):
        store.undo_task_edit()


def test_noop_preserves_previous_undo_and_title_edit_preserves_source_deadline(store):
    item = Assignment("189756", "source", "Report", "Instructions", NOW, NOW + timedelta(days=2), "source")
    task_id = store.create_assignment(item)["task_id"]
    first = store.edit_task_with_undo(task_id, "Renamed", item.deadline, "course", {})
    assert store.get_assignment(item.key)["assignment"] == item
    store.edit_task_with_undo(task_id, "Renamed", item.deadline, "course", {})
    assert store.get_task_action_state("undo")["token"] == first["undo_token"]
    store.clear_tasks()
    assert store.get_task_action_state("undo") is None


def test_failed_receipt_transaction_rolls_back_task_and_undo(tmp_path):
    db = Database(tmp_path / "rollback.db")
    db.initialize()
    task = db.create_task("Original", None, None, NOW, {})
    def crash():
        db.edit_task_with_undo(task["id"], "Changed", None, None, {})
        raise RuntimeError("Crash")
    with pytest.raises(RuntimeError):
        db.process_update(99, crash)
    assert db.list_open_tasks()[0]["title"] == "Original"
    assert db.get_task_action_state("undo") is None
    assert db.get_update(99) is None


def test_undo_and_receipt_share_transaction_and_restart(tmp_path):
    db = Database(tmp_path / "persist.db")
    db.initialize()
    task = db.create_task("Original", None, None, NOW, {})
    db.process_update(1, lambda: {"messages": [{"text": db.edit_task_with_undo(task["id"], "Changed", None, None, {})["title"]}]})
    db = Database(db.path)
    db.initialize()
    calls = []
    def undo():
        calls.append(True)
        return db.undo_task_edit()["title"]
    assert db.process_update(2, undo)["reply"] == "Original"
    assert db.process_update(2, undo)["reply"] == "Original"
    assert len(calls) == 1


def test_edit_undo_and_replayed_button_through_webhook(system):
    client, service, bot, config = system
    service.create("Original")
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    payload = {"update_id": 500, "message": {"chat": {"id": 123}, "text": '/edit 1 title: "Changed"'}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    token = main.db.get_task_action_state("undo")["token"]
    assert any("Undo this edit" in str(call.kwargs) for call in bot.send_message.await_args_list)
    callback = {"update_id": 501, "callback_query": {"id": "undo-cb", "from": {"id": 123},
                "data": f"task:undo:{token}", "message": {"message_id": 9, "chat": {"id": 123}}}}
    for _ in range(2):
        assert client.post('/telegram/webhook', headers=headers, json=callback).status_code == 200
    assert service.list_open()[0]["title"] == "Original"


def test_failed_edit_preserves_undo_and_retry_creates_undo(system, monkeypatch):
    from chronos.ai import AIError
    client, service, bot, config = system
    service.create("Original")
    asyncio.run(main.handle_message('/edit 1 title: "First"'))
    token = main.db.get_task_action_state("undo")["token"]
    monkeypatch.setattr(main.ai, "edit", AsyncMock(side_effect=AIError("Unavailable")))
    action = asyncio.run(main.prepare_message('/edit 1 change title', update_id=701))
    main.db.process_update(701, action)
    assert main.db.get_task_action_state("undo")["token"] == token
    monkeypatch.setattr(main.ai, "edit", AsyncMock(return_value=ParsedTask("Retried")))
    action = asyncio.run(main.prepare_pending_edit('edit:retry:701'))
    main.db.process_update(702, action)
    assert service.list_open()[0]["title"] == "Retried"
    assert "Edit undone" in asyncio.run(main.handle_message('/undo'))
    assert service.list_open()[0]["title"] == "First"

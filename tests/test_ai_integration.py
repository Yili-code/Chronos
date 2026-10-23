import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from chronos import main
from chronos.intents import Intent
from chronos.ai import AIError
from chronos.db import Database
from chronos.tasks import ParsedTask, TaskService


@pytest.fixture
def task_service(tmp_path, monkeypatch):
    db = Database(tmp_path / "integration.db")
    db.initialize()
    service = TaskService(db, main.settings.tz)
    monkeypatch.setattr(main, "tasks", service)
    monkeypatch.setattr(main.ai, "classify_intent", AsyncMock(return_value=Intent(action="task_create")))
    async def parse_many(text, *args, **kwargs):
        return [await main.ai.parse(text, *args, **kwargs)]
    monkeypatch.setattr(main.ai, "parse_many", AsyncMock(side_effect=parse_many))
    return service


def test_telegram_uses_external_ai(task_service, monkeypatch):
    parse = AsyncMock(return_value=ParsedTask("Review external analysis"))
    monkeypatch.setattr(main.ai, "parse", parse)
    reply = asyncio.run(main.handle_message("新增 工作"))
    assert reply.startswith("<b>Task created</b>")
    assert "<b>Review external analysis</b>" in reply
    assert "<b>Due</b>" not in reply
    assert task_service.list_open()[0]["due_at"] is None
    assert "<b>Tasks</b>\n1. " in reply
    assert parse.await_count == 1
    due = datetime(2026, 9, 20, 12, tzinfo=main.settings.tz)
    edit = AsyncMock(return_value=ParsedTask("Review external analysis", due))
    monkeypatch.setattr(main.ai, "edit", edit)
    reply = asyncio.run(main.handle_message("/edit 1 改到週日中午"))
    assert "<b>Updated · Task 1</b>" in reply
    assert "<b>After</b> 09/20 (Sun) 12:00" in reply
    assert "<b>Tasks</b>" in reply
    assert task_service.list_open()[0]["due_at"] == due.isoformat()


def test_failure_does_not_change_tasks(task_service, monkeypatch):
    task_service.create("Original task")
    before = task_service.list_open()
    monkeypatch.setattr(main.ai, "parse", AsyncMock(side_effect=AIError("External AI failed")))
    monkeypatch.setattr(main.ai, "edit", AsyncMock(side_effect=AIError("External AI failed")))
    assert "External AI failed" in asyncio.run(main.handle_message("新增 工作"))
    edit_failure = asyncio.run(main.handle_message("/edit 1 改到明天"))
    assert "External AI failed" in edit_failure
    assert "Task 1 is unchanged" in edit_failure
    assert asyncio.run(main.handle_message("/reschedule 1 明天")) == "Unknown command. Use /info to see available commands."
    assert task_service.list_open() == before
    assert "Original task" in asyncio.run(main.handle_message("/tasks"))
    assert asyncio.run(main.handle_message("/done 1")) == "Completed Original task\n\nNo open tasks. All clear."


def test_edit_uses_natural_language_and_returns_latest_list(task_service, monkeypatch):
    original = task_service.create("Draft launch plan", project="Chronos")
    due = datetime(2026, 10, 3, 18, tzinfo=main.settings.tz)
    edit = AsyncMock(return_value=ParsedTask("Finalize launch plan", due, None))
    monkeypatch.setattr(main.ai, "edit", edit)
    reply = asyncio.run(main.handle_message("/edit 1 改成完成 launch plan，期限 10/03 18:00 並移除專案"))
    edit.assert_awaited_once()
    assert edit.call_args.args[0]["id"] == original["id"]
    assert "<b>Updated · Task 1</b>" in reply
    assert "<b>Before</b> Draft launch plan" in reply
    assert "<b>After</b> Finalize launch plan" in reply
    assert "<b>After</b> 10/03 (Sat) 18:00" in reply
    assert "<b>Tasks</b>\n1. 🚨 <b>Finalize launch plan</b>" in reply
    stored = task_service.list_open()[0]
    assert stored["title"] == "Finalize launch plan"
    assert stored["project"] is None


def test_edit_missing_position_skips_ai(task_service, monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr(main.ai, "edit", edit)
    assert asyncio.run(main.handle_message("/edit 1 rename it")) == "Task 1 not found.\n\nNo open tasks. All clear."
    edit.assert_not_awaited()

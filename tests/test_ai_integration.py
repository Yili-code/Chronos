import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from chronos import main
from chronos.ai import AIError
from chronos.db import Database
from chronos.tasks import ParsedTask, TaskService


@pytest.fixture
def task_service(tmp_path, monkeypatch):
    db = Database(tmp_path / "integration.db")
    db.initialize()
    service = TaskService(db, main.settings.tz)
    monkeypatch.setattr(main, "tasks", service)
    return service


def test_web_and_telegram_use_external_ai(task_service, monkeypatch):
    parse = AsyncMock(return_value=ParsedTask("Review external analysis"))
    monkeypatch.setattr(main.ai, "parse", parse)
    task = asyncio.run(main.create_natural_task(main.NaturalTask(text="工作")))
    assert task["title"] == "Review external analysis"
    assert asyncio.run(main.handle_message("新增 工作")) == "Created: Review external analysis"
    assert parse.await_count == 2
    due = datetime(2026, 9, 20, 12, tzinfo=main.settings.tz)
    parse.return_value = ParsedTask("Reschedule task", due)
    reply = asyncio.run(main.handle_message("/reschedule 1 週日中午"))
    assert "Rescheduled: Review external analysis | 09/20 12:00" in reply
    assert "Open tasks:" in reply
    assert task_service.list_open()[0]["due_at"] == due.isoformat()


def test_failure_does_not_change_tasks(task_service, monkeypatch):
    task_service.create("Original task")
    before = task_service.list_open()
    monkeypatch.setattr(main.ai, "parse", AsyncMock(side_effect=AIError("External AI failed")))
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.create_natural_task(main.NaturalTask(text="工作")))
    assert error.value.status_code == 503
    assert "External AI failed" in asyncio.run(main.handle_message("新增 工作"))
    assert "External AI failed" in asyncio.run(main.handle_message("/reschedule 1 明天"))
    assert task_service.list_open() == before
    assert "Original task" in asyncio.run(main.handle_message("/tasks"))
    assert asyncio.run(main.handle_message("/done 1")) == "Completed: Original task\n\nNo open tasks."

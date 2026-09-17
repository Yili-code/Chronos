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
    parse = AsyncMock(return_value=ParsedTask("外接解析結果"))
    monkeypatch.setattr(main.ai, "parse", parse)
    task = asyncio.run(main.create_natural_task(main.NaturalTask(text="工作")))
    assert task["title"] == "外接解析結果"
    assert "外接解析結果" in asyncio.run(main.handle_message("新增 工作"))
    assert parse.await_count == 2
    due = datetime(2026, 9, 20, 12, tzinfo=main.settings.tz)
    parse.return_value = ParsedTask("更新期限", due)
    assert "已延期" in asyncio.run(main.handle_message(f"延期 {task['id']} 到週日中午"))
    assert task_service.list_open()[0]["due_at"] == due.isoformat()


def test_failure_does_not_change_tasks(task_service, monkeypatch):
    task = task_service.create("原始工作")
    before = task_service.list_open()
    monkeypatch.setattr(main.ai, "parse", AsyncMock(side_effect=AIError("外部 AI 失敗")))
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.create_natural_task(main.NaturalTask(text="工作")))
    assert error.value.status_code == 503
    assert "外部 AI 失敗" in asyncio.run(main.handle_message("新增 工作"))
    assert "外部 AI 失敗" in asyncio.run(main.handle_message(f"延期 {task['id']} 到明天"))
    assert task_service.list_open() == before
    assert "原始工作" in asyncio.run(main.handle_message("代辦"))
    assert asyncio.run(main.handle_message(f"完成 {task['id']}")) == "已完成。"

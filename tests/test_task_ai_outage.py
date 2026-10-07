import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from chronos import main
from chronos.ai import AIError, ExternalAI
from chronos.tasks import parse_deterministic_edit
from test_ai import config, mock_provider
from test_system import system
from test_task_edit_webhook import send


@pytest.fixture(autouse=True)
def stub_edit(monkeypatch):
    monkeypatch.setattr(main.ai, "edit", AsyncMock(side_effect=AssertionError("Unexpected AI edit")))


def test_literal_add_is_idempotent_and_never_calls_ai(system):
    client, tasks, bot, settings = system
    main.ai.parse.side_effect = AssertionError("AI unavailable")
    for _ in range(2):
        assert send(client, 8100, "/add Review Graph Algorithms Quiz 1 #Graph").status_code == 200
    items = tasks.list_open()
    assert len(items) == 1
    assert items[0]["title"] == "Review Graph Algorithms Quiz 1"
    assert items[0]["project"] == "Graph"
    assert items[0]["due_at"] is None
    main.ai.parse.assert_not_awaited()


@pytest.mark.parametrize("instruction", [
    'Set the title to "New title". Keep the existing due date and tag.',
    'Translate the title to "New title". Keep other fields unchanged.',
    'title: "New title"',
])
def test_exact_title_edit_preserves_legacy_fields_without_ai(system, instruction):
    client, tasks, bot, settings = system
    old = tasks.create("舊標題", datetime(2026, 10, 14, 23, 59, tzinfo=settings.tz),
                       "資訊安全實務與管理", {"event": "2026-10-05"})
    main.ai.edit.side_effect = AssertionError("AI unavailable")
    assert send(client, 8101, "/edit 1 " + instruction).status_code == 200
    new = tasks.get_open_by_id(old["id"])
    assert new["title"] == "New title"
    for field in ("due_at", "project", "timing"):
        assert new[field] == old[field]
    main.ai.edit.assert_not_awaited()


def test_exact_title_does_not_silently_ignore_extra_instructions():
    current = {"title": "Before", "due_at": None, "project": None}
    assert parse_deterministic_edit(current, 'Set the title to "After" and remove the deadline') is None


@pytest.mark.parametrize("command", ["/add", '/edit 1 title: "   "', '/edit 1 title: "' + 'x' * 2001 + '"'])
def test_invalid_literal_input_does_not_mutate(system, command):
    client, tasks, bot, settings = system
    tasks.create("Keep me")
    before = tasks.list_open()
    assert send(client, 8102, command).status_code == 200
    assert tasks.list_open() == before
    main.ai.parse.assert_not_awaited()
    main.ai.edit.assert_not_awaited()


def test_saved_exact_edit_recovers_after_outage_and_reordering(system):
    client, tasks, bot, settings = system
    original = tasks.create("Before")
    main.db.save_pending_task_edit(8200, original["id"], 1,
        'Set the title to "After". Keep other fields unchanged.', datetime.now(settings.tz))
    tasks.create("Urgent", datetime(2026, 10, 8, tzinfo=settings.tz))
    main.ai.edit.side_effect = AssertionError("AI unavailable")
    action = asyncio.run(main.prepare_pending_edit("edit:retry:8200"))
    main.db.process_update(8201, action)
    assert tasks.get_open_by_id(original["id"])["title"] == "After"
    assert tasks.list_open()[0]["title"] == "Urgent"
    assert main.db.get_pending_task_edit(8200) is None
    main.ai.edit.assert_not_awaited()


def test_ai_edit_keeps_legacy_tag(monkeypatch):
    operations = {name: {"op": "keep", "value": None} for name in
                  ("title", "due_at", "project", "due_date", "scheduled", "event", "uncertain")}
    operations["title"] = {"op": "set", "value": "Confirm course coverage"}
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={"candidates": [
        {"content": {"parts": [{"text": json.dumps(operations)}]}}]}))
    parsed = asyncio.run(ExternalAI(config()).edit({
        "title": "確認上課範圍", "project": "資訊安全實務與管理", "due_at": None,
    }, "Translate title to English"))
    assert parsed.title == "Confirm course coverage"
    assert parsed.project == "資訊安全實務與管理"


def test_edit_preserves_safe_error_reason_on_initial_and_retry(system):
    client, tasks, bot, settings = system
    tasks.create("Before")
    main.ai.edit.side_effect = AIError("The configured AI model is unavailable. No task was changed.")
    assert send(client, 8300, "/edit 1 shorten it").status_code == 200
    assert "configured AI model is unavailable" in bot.send_message.call_args.args[1]
    output = (asyncio.run(main.prepare_pending_edit("edit:retry:8300")))()
    assert "configured AI model is unavailable" in str(output)
    assert tasks.list_open()[0]["title"] == "Before"

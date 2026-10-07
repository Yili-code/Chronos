from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from chronos import main
from chronos.tasks import ParsedTask
from test_system import system


def send(client, update_id, text, *, reply_to=None, edited=False, chat_id=123):
    message = {"message_id": update_id + 100, "chat": {"id": chat_id}, "text": text}
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to}
    return client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"},
                       json={"update_id": update_id, "edited_message" if edited else "message": message})


def test_reply_to_created_card_edits_original_task_after_reorder(system, monkeypatch):
    client, tasks, bot, config = system
    bot.send_message = AsyncMock(return_value={"ok": True, "result": {"message_id": 500}})
    main.ai.parse.return_value = ParsedTask("Finish remote lecture", datetime(2026, 10, 10, 9, tzinfo=config.tz))
    assert send(client, 1000, "finish remote lecture in 星期六").status_code == 200
    original = tasks.list_open()[0]
    urgent = tasks.create("Urgent", datetime(2026, 10, 8, 9, tzinfo=config.tz))
    bot.send_message = AsyncMock(side_effect=[{"ok": True, "result": {"message_id": 501}}, {"ok": True}])
    edit = AsyncMock(side_effect=AssertionError("no AI needed"))
    monkeypatch.setattr(main.ai, "edit", edit)
    for _ in range(2):
        assert send(client, 1001, "星期六是課程的時間非 due time", reply_to=500).status_code == 200
    stored = tasks.get_open_by_id(original["id"])
    assert stored["due_at"] is None and stored["timing"]["event"] == "2026-10-10"
    assert tasks.get_open_by_id(urgent["id"])["due_at"] == urgent["due_at"]
    assert len(tasks.list_open()) == 2
    assert bot.send_message.await_count == 2
    edit.assert_not_awaited()


@pytest.mark.parametrize("text", ["/edit 1 移除期限", "/done 1", "/clear", "finish lecture", "/export " + "a" * 64])
def test_edited_messages_are_explicitly_not_replayed(system, text):
    client, tasks, bot, config = system
    tasks.create("Keep me", datetime(2026, 10, 10, 9, tzinfo=config.tz))
    before = tasks.list_open()
    for _ in range(2):
        assert send(client, 1100, text, edited=True).status_code == 200
    assert tasks.list_open() == before
    assert "Editing a sent message does not change tasks" in bot.send_message.call_args.args[1]
    assert bot.send_message.await_count == 1
    main.ai.parse.assert_not_awaited()


def test_edited_message_auth_and_closed_task_reply(system):
    client, tasks, bot, config = system
    assert send(client, 1101, "/clear", edited=True, chat_id=999).status_code == 403
    task = tasks.create("Finished")
    main.db.bind_task_message(123, 800, task["id"])
    tasks.complete(task["id"])
    assert send(client, 1102, "移除期限", reply_to=800).status_code == 200
    assert "not found" in bot.send_message.call_args.args[1]
    main.ai.parse.assert_not_awaited()


def test_task_button_binds_reply_prompt_to_fixed_id(system):
    client, tasks, bot, config = system
    task = tasks.create("Lecture")
    bot.send_message = AsyncMock(return_value={"ok": True, "result": {"message_id": 900}})
    response = client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}, json={
        "update_id": 1200, "callback_query": {"id": "callback", "data": f"task:edit:{task['id']}",
        "message": {"message_id": 899, "chat": {"id": 123}, "text": "Tasks"}}})
    assert response.status_code == 200
    assert main.db.task_for_message(123, 900) == task["id"]
    assert bot.send_message.call_args.kwargs["reply_markup"]["force_reply"] is True

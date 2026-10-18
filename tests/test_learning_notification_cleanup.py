from unittest.mock import AsyncMock, Mock

from chronos import main
from test_system import system


def test_callback_deletes_original_only_after_reply_is_delivered(system, monkeypatch):
    client, _, bot, config = system
    config.enable_learning_calendar = True
    planner = Mock()
    planner.callback = AsyncMock(return_value="Updated proposal")
    planner.tick = AsyncMock()
    monkeypatch.setattr(main, "learning_calendar", lambda: planner)
    payload = {"update_id": 98901, "callback_query": {
        "id": "cleanup", "data": "learn:edit:1:" + "a" * 32,
        "message": {"message_id": 50, "chat": {"id": 123}}}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    bot.send_message.return_value = {"ok": False}
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 502
    bot.request.assert_not_awaited()
    bot.send_message.return_value = {"ok": True}
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    bot.request.assert_awaited_with("deleteMessage", {"chat_id": 123, "message_id": 50})
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert planner.callback.await_count == 1
    assert bot.send_message.await_count == 2

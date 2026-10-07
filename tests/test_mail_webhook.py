from unittest.mock import AsyncMock

from chronos import main
from chronos.tasks import ParsedTask
from test_system import system


def enable(config, monkeypatch):
    config.enable_gmail = True
    config.gmail_account = "owner@example.com"
    config.gmail_client_id = "client"
    config.gmail_client_secret = "secret"
    config.gmail_refresh_token = "refresh"
    gmail = AsyncMock()
    monkeypatch.setattr(main, "gmail", gmail)
    mail = main.mail_workflow()
    mail.patch("message:ab12", mail={"subject": "Finish report", "body": "Report", "snippet": "Report"})
    mail.patch("telegram:123:500", mail_id="ab12")
    return gmail


def post(client, update, text="新增任務：完成報告", *, callback=None, edited=False, chat=123, card=500):
    message = {"message_id": card if callback else 600, "chat": {"id": chat}, "text": text}
    if callback:
        payload = {"update_id": update, "callback_query": {"id": "query", "data": callback, "message": message}}
    else:
        message["reply_to_message"] = {"message_id": card}
        payload = {"update_id": update, "edited_message" if edited else "message": message}
    return client.post("/telegram/webhook", json=payload,
                       headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"})


def test_reply_to_mail_creates_one_task_and_duplicate_update_does_not_repeat(system, monkeypatch):
    client, tasks, bot, config = system
    enable(config, monkeypatch)
    main.ai.parse.return_value = ParsedTask("Finish report")
    for _ in range(2):
        assert post(client, 5001).status_code == 200
    assert len(tasks.list_open()) == 1
    assert bot.send_message.await_count == 1
    main.ai.parse.assert_awaited_once()


def test_callback_must_match_actual_card_and_owner(system, monkeypatch):
    client, tasks, bot, config = system
    gmail = enable(config, monkeypatch)
    assert post(client, 5002, callback="mail:trash:ab12", card=999).status_code == 403
    assert post(client, 5003, callback="mail:trash:other").status_code == 403
    assert post(client, 5004, callback="mail:trash:ab12", chat=456).status_code == 403
    gmail.trash.assert_not_awaited()
    for _ in range(2):
        assert post(client, 5005, callback="mail:trash:ab12").status_code == 200
    gmail.trash.assert_awaited_once_with("ab12")


def test_editing_mail_instruction_does_not_replay_and_send_never_executes(system, monkeypatch):
    client, tasks, bot, config = system
    gmail = enable(config, monkeypatch)
    assert post(client, 5006, text="刪除", edited=True).status_code == 200
    assert post(client, 5007, text="寄出這封信").status_code == 200
    gmail.trash.assert_not_awaited()
    main.ai.parse.assert_not_awaited()
    assert not tasks.list_open()


def test_existing_daily_endpoint_runs_mail_when_enabled(system, monkeypatch):
    client, tasks, bot, config = system
    mail = AsyncMock(return_value={"processed": 0})
    monkeypatch.setattr(main, "send_daily_mail", mail)
    assert client.post("/internal/daily").status_code == 403
    mail.assert_not_awaited()
    response = client.post("/internal/daily", headers={"X-Chronos-Scheduler-Secret": "test-scheduler"})
    assert response.status_code == 200
    mail.assert_awaited_once()


def test_mail_status_is_authenticated_and_read_only(system, monkeypatch):
    client, tasks, bot, config = system
    gmail = enable(config, monkeypatch)
    gmail.verify_account.return_value = config.gmail_account
    bot.request.return_value = {"ok": True, "result": {"id": 123}}
    assert client.get("/internal/mail/status").status_code == 403
    gmail.verify_account.assert_not_awaited()
    result = client.get("/internal/mail/status", headers={"X-Chronos-Scheduler-Secret": "test-scheduler"})
    assert result.status_code == 200
    assert result.json()["send_enabled"] is False
    gmail.trash.assert_not_awaited()
    gmail.mark_read.assert_not_awaited()
    bot.send_message.assert_not_awaited()

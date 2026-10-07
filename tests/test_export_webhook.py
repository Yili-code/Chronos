from unittest.mock import AsyncMock
import pytest
from chronos import main
from chronos.telegram import TelegramError
from test_system import system
from test_note_record import note


def request_payload(chat=123):
    return {"update_id": 701, "message": {"message_id": 801,
            "chat": {"id": chat}, "text": "/export " + "a" * 64}}


def test_export_webhook_sends_canonical_attachment_once(system, monkeypatch):
    client, _, bot, _ = system
    record = note()
    main.db.save_study_note(record)
    sender = AsyncMock(return_value={"ok": True, "result": {"message_id": 99, "document": {"file_id": "fake"}}})
    monkeypatch.setattr(bot, "send_document", sender)
    for _ in range(2):
        response = client.post("/telegram/webhook", json=request_payload(),
                               headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"})
        assert response.status_code == 200
        assert response.json()["document_status"] == "sent"
    sender.assert_awaited_once_with(123, *record.export())
    bot.send_message.assert_not_awaited()
    main.ai.parse.assert_not_awaited()


@pytest.mark.parametrize("chat,secret", [(999, "test-hook"), (123, "wrong")])
def test_export_denies_unauthorized_requests_before_read(system, monkeypatch, chat, secret):
    client, _, bot, _ = system
    sender = AsyncMock()
    monkeypatch.setattr(bot, "send_document", sender)
    def forbidden_read(_key):
        raise AssertionError("unauthorized request read notes")
    monkeypatch.setattr(main.db, "get_study_note", forbidden_read)
    response = client.post("/telegram/webhook", json=request_payload(chat),
                           headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    assert response.status_code == 403
    sender.assert_not_awaited()


def test_missing_export_returns_help_without_generation(system, monkeypatch):
    client, _, bot, _ = system
    sender = AsyncMock()
    monkeypatch.setattr(bot, "send_document", sender)
    response = client.post("/telegram/webhook", json=request_payload(),
                           headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"})
    assert response.status_code == 200
    assert "No exportable note was found" in bot.send_message.call_args.args[1]
    sender.assert_not_awaited()
    main.ai.parse.assert_not_awaited()


def test_uncertain_export_acknowledges_without_blind_retry(system, monkeypatch):
    client, _, bot, _ = system
    main.db.save_study_note(note())
    sender = AsyncMock(side_effect=TelegramError("unknown"))
    monkeypatch.setattr(bot, "send_document", sender)
    for _ in range(2):
        response = client.post("/telegram/webhook", json=request_payload(),
                               headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"})
        assert response.json()["document_status"] == "uncertain"
    assert sender.await_count == 1

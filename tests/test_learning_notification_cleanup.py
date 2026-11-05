from unittest.mock import AsyncMock, Mock

from chronos import main
from test_system import system, observe_message_lifecycle
import pytest


@pytest.fixture(autouse=True)
def real_notice_lifetime(system):
    observe_message_lifecycle(system[2])


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


def test_completion_ack_is_deleted_after_seven_seconds_and_not_replayed(system,monkeypatch):
    client,_,bot,config=system
    config.enable_learning_calendar=True
    planner=Mock()
    planner.callback=AsyncMock(return_value=main.PROGRESS_COMPLETED_ACK)
    planner.tick=AsyncMock()
    monkeypatch.setattr(main,'learning_calendar',lambda:planner)
    delay=AsyncMock()
    monkeypatch.setattr(main.asyncio,'sleep',delay)
    bot.expiry.sleep=delay
    bot.send_message.return_value={'ok':True,'result':{'message_id':901}}
    payload={'update_id':98902,'callback_query':{'id':'done','data':'learn:done:1:'+'a'*32,
        'message':{'message_id':50,'chat':{'id':123}}}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',json=payload,headers=headers).status_code==200
    delay.assert_awaited_once()
    assert 0 <= delay.await_args.args[0] <= 7
    assert any(call.args==('deleteMessage',{'chat_id':123,'message_id':901}) for call in bot.request.await_args_list)
    assert client.post('/telegram/webhook',json=payload,headers=headers).status_code==200
    assert bot.send_message.await_count==1 and delay.await_count==1


def test_failed_completion_ack_send_does_not_wait_or_delete(system,monkeypatch):
    client,_,bot,config=system
    config.enable_learning_calendar=True
    planner=Mock(callback=AsyncMock(return_value=main.PROGRESS_COMPLETED_ACK),tick=AsyncMock())
    monkeypatch.setattr(main,'learning_calendar',lambda:planner)
    delay=AsyncMock()
    monkeypatch.setattr(main.asyncio,'sleep',delay)
    bot.expiry.sleep=delay
    bot.send_message.return_value={'ok':False}
    payload={'update_id':98903,'callback_query':{'id':'done','data':'learn:done:1:'+'a'*32,
        'message':{'message_id':50,'chat':{'id':123}}}}
    assert client.post('/telegram/webhook',json=payload,headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}).status_code==502
    delay.assert_not_awaited()
    bot.request.assert_not_awaited()


def test_plain_command_completion_ack_uses_the_same_seven_second_cleanup(system,monkeypatch):
    client,_,bot,_=system
    monkeypatch.setattr(main,'prepare_message',AsyncMock(return_value=lambda:main.PROGRESS_COMPLETED_ACK))
    delay=AsyncMock()
    monkeypatch.setattr(main.asyncio,'sleep',delay)
    bot.expiry.sleep=delay
    bot.send_message.return_value={'ok':True,'result':{'message_id':902}}
    payload={'update_id':98904,'message':{'message_id':51,'chat':{'id':123},'text':'/studyfeedback 1 60 done'}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',json=payload,headers=headers).status_code==200
    delay.assert_awaited_once()
    assert 0 <= delay.await_args.args[0] <= 7
    bot.request.assert_awaited_with('deleteMessage',{'chat_id':123,'message_id':902})
    assert client.post('/telegram/webhook',json=payload,headers=headers).status_code==200
    assert bot.send_message.await_count==1

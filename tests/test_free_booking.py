import asyncio
from datetime import datetime,timedelta
from unittest.mock import AsyncMock

import pytest
from chronos.ai import EventTitleOutput
from chronos.free_booking import bind_free_message,preview_free_event,free_booking_callback
from chronos.settings import Settings
from test_deadline_reminders import store
from test_system import system
from chronos import main

NOW=datetime.fromisoformat('2026-10-18T08:00:00+08:00')
CFG=Settings(_env_file=None,telegram_chat_id=123)


def setup_slot(db):
    slot={'start':'2026-10-18T09:00:00+08:00','end':'2026-10-18T11:00:00+08:00','expires_at':(NOW+timedelta(hours=1)).isoformat()}
    bind_free_message(db,123,10,{'free_slot':slot})
    ai=AsyncMock();ai.calendar_event_title.return_value=EventTitleOutput(title='Coffee with friends',actionable=True)
    result=asyncio.run(preview_free_event(db,ai,CFG,123,10,'和朋友約咖啡廳聊天',88,NOW))
    message=result['messages'][0];bind_free_message(db,123,11,message)
    return message['free_booking'],ai


def test_reply_previews_personal_event_then_allow_rechecks_and_books_once(store):
    token,ai=setup_slot(store)
    cal=AsyncMock();cal.event.return_value=None;cal.busy.return_value=[]
    async def insert(body):
        cal.event.return_value=body
        return body
    cal.insert.side_effect=insert
    assert store.list_open_tasks()==[]
    reply=asyncio.run(free_booking_callback(store,cal,CFG,123,11,token,'allow',NOW))
    assert 'Added to Calendar' in reply and 'Coffee with friends' in reply
    asyncio.run(free_booking_callback(store,cal,CFG,123,11,token,'allow',NOW))
    cal.insert.assert_awaited_once()
    assert cal.insert.await_args.args[0]['colorId'] == '6'
    assert store.list_open_tasks()==[]


@pytest.mark.parametrize('failure',['cancel','busy','wrong_message','expired'])
def test_no_booking_on_cancel_conflict_wrong_card_or_expiry(store,failure):
    token,_=setup_slot(store)
    cal=AsyncMock();cal.event.return_value=None;cal.busy.return_value=[]
    if failure=='busy':cal.busy.return_value=[(NOW.replace(hour=9),NOW.replace(hour=11))]
    asyncio.run(free_booking_callback(store,cal,CFG,123,999 if failure=='wrong_message' else 11,
        token,'cancel' if failure=='cancel' else 'allow',NOW+timedelta(hours=2) if failure=='expired' else NOW))
    cal.insert.assert_not_awaited()


def test_new_reply_supersedes_old_preview_and_lost_insert_response_reconciles(store):
    old,ai=setup_slot(store)
    result=asyncio.run(preview_free_event(store,ai,CFG,123,10,'New title',89,NOW))
    token=result['messages'][0]['free_booking'];bind_free_message(store,123,12,result['messages'][0])
    cal=AsyncMock();cal.event.return_value=None;cal.busy.return_value=[]
    assert 'no longer active' in asyncio.run(free_booking_callback(store,cal,CFG,123,11,old,'allow',NOW))
    async def uncertain(body):
        cal.event.return_value=body
        raise TimeoutError()
    cal.insert.side_effect=uncertain
    with pytest.raises(TimeoutError):
        asyncio.run(free_booking_callback(store,cal,CFG,123,12,token,'allow',NOW))
    assert 'Added' in asyncio.run(free_booking_callback(store,cal,CFG,123,12,token,'allow',NOW))
    cal.insert.assert_awaited_once()


def test_webhook_free_reply_binds_preview_and_does_not_create_task(system,monkeypatch):
    client,tasks,bot,cfg=system
    now=datetime.now(cfg.tz)
    slot={'start':(now+timedelta(days=1)).replace(hour=9).isoformat(),
          'end':(now+timedelta(days=1)).replace(hour=11).isoformat(),'expires_at':(now+timedelta(hours=1)).isoformat()}
    bind_free_message(main.db,123,50,{'free_slot':slot})
    monkeypatch.setattr(main.ai,'calendar_event_title',AsyncMock(return_value=EventTitleOutput(title='Coffee with friends',actionable=True)))
    bot.send_message.return_value={'ok':True,'result':{'message_id':51}}
    result=client.post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json={
        'update_id':90801,'message':{'message_id':60,'chat':{'id':123},'text':'和朋友約咖啡廳聊天',
        'reply_to_message':{'message_id':50,'chat':{'id':123}}}})
    assert result.status_code==200
    assert 'Coffee with friends' in bot.send_message.await_args.args[1]
    assert tasks.list_open()==[]
    main.ai.parse.assert_not_awaited()

@pytest.mark.parametrize('success',[True,False])
def test_allow_deletes_only_successful_preview_after_confirmation(system,monkeypatch,success):
    client,tasks,bot,cfg=system
    token='c'*32;key='free:booking:'+token
    main.db.mutate_task_action_state(key,lambda old:{'status':'pending','chat_id':123,'message_id':11})
    async def callback(*args):
        main.db.mutate_task_action_state(key,lambda old:{**old,'status':'booked' if success else 'creating'})
        if not success:raise main.CalendarError('offline')
        return 'Added to Calendar\nCoffee with friends'
    handler=AsyncMock(side_effect=callback)
    monkeypatch.setattr(main,'free_booking_callback',handler)
    bot.request.return_value={'ok':True}
    payload={'update_id':90803,'callback_query':{'id':'allow','data':'free:allow:'+token,
        'message':{'message_id':11,'chat':{'id':123}}}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    deletions=[call for call in bot.request.await_args_list if call.args[0]=='deleteMessage']
    assert bool(deletions)==success
    if success:
        assert deletions[0].args[1]=={'chat_id':123,'message_id':11}
        assert 'Added to Calendar' in bot.send_message.await_args.args[1]
        assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
        assert bot.send_message.await_count==1
        assert handler.await_count==1


def test_preview_delete_failure_retries_cleanup_without_rebooking(system,monkeypatch):
    client,tasks,bot,cfg=system
    token='d'*32;key='free:booking:'+token
    main.db.mutate_task_action_state(key,lambda old:{'status':'booked','chat_id':123,'message_id':11})
    handler=AsyncMock(return_value='Added to Calendar\nCoffee with friends')
    monkeypatch.setattr(main,'free_booking_callback',handler)
    bot.request.return_value={'ok':False,'error_code':500}
    payload={'update_id':90804,'callback_query':{'id':'allow','data':'free:allow:'+token,
        'message':{'message_id':11,'chat':{'id':123}}}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==502
    bot.request.return_value={'ok':False,'error_code':400,'description':'Bad Request: message to delete not found'}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    assert handler.await_count==1
    assert bot.send_message.await_count==1

"""State-pressure checks after the 100 synthetic command execution corpus."""
from copy import deepcopy
from datetime import datetime,timedelta
from unittest.mock import AsyncMock
import pytest
from chronos import main,calendar_replan
from test_system import system
from test_intents import store
from test_command_reliability import command_scenario,command_model_config


def post(client,update,text=None,data=None,message_id=500):
    payload={'update_id':update}
    if data is None:payload['message']={'message_id':update,'chat':{'id':123},'text':text}
    else:payload['callback_query']={'id':'callback-'+str(update),'data':data,'message':{'message_id':message_id,'chat':{'id':123}}}
    return client.post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json=payload)


def test_clear_cancellation_revokes_old_confirmation(system,store):
    client,tasks,bot,cfg=system
    task=main.tasks.create('Keep after cancel')
    assert post(client,8001,text='/clear').status_code==200
    keyboard=bot.send_message.await_args.kwargs['reply_markup']['inline_keyboard']
    allow=keyboard[0][0]['callback_data'];cancel=keyboard[1][0]['callback_data']
    assert post(client,8002,data=cancel).status_code==200
    assert post(client,8003,data=allow).status_code==200
    assert store.get_task(task['id'])['status']=='open'


def test_clear_consumed_confirmation_cannot_clear_later_tasks(system,store):
    client,tasks,bot,cfg=system
    main.tasks.create('First set')
    assert post(client,8011,text='/clear').status_code==200
    allow=bot.send_message.await_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data']
    assert post(client,8012,data=allow).status_code==200
    assert not store.list_open_tasks()
    later=main.tasks.create('Later task')
    assert post(client,8013,data=allow).status_code==200
    assert store.get_task(later['id'])['status']=='open'


@pytest.mark.parametrize('change',['new_request','expired','wrong_chat','legacy'])
def test_clear_confirmation_scope_and_expiry(system,store,change):
    from chronos.clear_confirmation import key
    client,tasks,bot,cfg=system
    task=main.tasks.create('Keep scope')
    assert post(client,8031,text='/clear').status_code==200
    allow=bot.send_message.await_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data']
    if change=='new_request':assert post(client,8032,text='/clear').status_code==200
    if change=='expired':store.mutate_task_action_state(key(123),lambda s:{**s,'expires_at':(datetime.now(cfg.tz)-timedelta(seconds=1)).isoformat()})
    if change=='wrong_chat':store.mutate_task_action_state(key(123),lambda s:{**s,'chat_id':999})
    if change=='legacy':allow='clear:confirm'
    assert post(client,8033,data=allow).status_code==200
    assert store.get_task(task['id'])['status']=='open'


def test_clear_failed_delivery_retry_preserves_request_and_mutates_once(system,store):
    from chronos.clear_confirmation import key
    client,tasks,bot,cfg=system
    main.tasks.create('Original')
    bot.send_message.side_effect=[{'ok':False},{'ok':True}]
    assert post(client,8041,text='/clear').status_code==502
    state=store.get_task_action_state(key(123))
    assert post(client,8041,text='/clear').status_code==200
    assert store.get_task_action_state(key(123))==state
    allow='clear:confirm:'+state['token']
    bot.send_message.side_effect=[{'ok':False},{'ok':True}]
    assert post(client,8042,data=allow).status_code==502
    assert not store.list_open_tasks()
    later=main.tasks.create('Added after uncertain reply')
    assert post(client,8042,data=allow).status_code==200
    assert store.get_task(later['id'])['status']=='open'
    bot.send_message.side_effect=None
    bot.send_message.return_value={'ok':True}
    assert post(client,8043,data=allow).status_code==200
    assert store.get_task(later['id'])['status']=='open'


def test_clear_then_undo_does_not_resurrect_removed_tasks(system,store):
    client,tasks,bot,cfg=system
    task=main.tasks.create('Original')
    store.edit_task_with_undo(task['id'],'Edited',None,None,{})
    assert post(client,8051,text='/clear').status_code==200
    allow=bot.send_message.await_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data']
    assert post(client,8052,data=allow).status_code==200
    assert post(client,8053,text='/undo').status_code==200
    assert not store.list_open_tasks()


def test_failed_model_receipt_never_later_replays_creation(system,store,monkeypatch):
    from chronos.ai import AIError
    from chronos.intents import Intent
    client,tasks,bot,cfg=system
    classifier=AsyncMock(side_effect=[AIError('synthetic unavailable'),Intent(action='task_create',instruction='Create a new report')])
    monkeypatch.setattr(main.ai,'classify_intent',classifier)
    assert post(client,8061,text='Create a new report').status_code==200
    assert not store.list_open_tasks()
    assert post(client,8061,text='Create a new report').status_code==200
    assert classifier.await_count==1 and not store.list_open_tasks()
    assert post(client,8062,text='Create a new report').status_code==200
    assert len(store.list_open_tasks())==1


def test_literal_position_survives_list_reordering_during_model_call(system,store,monkeypatch):
    from chronos.intents import Intent
    client,tasks,bot,cfg=system
    first=main.tasks.create('First report');target=main.tasks.create('Second report')
    async def classify(*args,**kwargs):
        main.tasks.create('Urgent',datetime.now(cfg.tz)+timedelta(hours=1))
        return Intent(action='task_complete',task_ids=[first['id']],instruction=args[0])
    monkeypatch.setattr(main.ai,'classify_intent',AsyncMock(side_effect=classify))
    # This is deliberately outside the narrow complete-command grammar, so a
    # real provider boundary can reorder the list while its original meaning stays pinned.
    assert post(client,8071,text='我剛整理完 task 2 的整份報告，請標記完成').status_code==200
    assert store.get_task(target['id'])['status']=='done'
    assert store.get_task(first['id'])['status']=='open'


def test_changed_target_during_model_call_is_not_completed(system,store,monkeypatch):
    from chronos.intents import Intent
    client,tasks,bot,cfg=system
    target=main.tasks.create('Second report')
    async def classify(*args,**kwargs):
        store.edit_task_with_undo(target['id'],'Changed by another update',None,None,{})
        return Intent(action='task_complete',task_ids=[target['id']],instruction=args[0])
    monkeypatch.setattr(main.ai,'classify_intent',AsyncMock(side_effect=classify))
    assert post(client,8081,text='Second report 已經完整完成').status_code==200
    assert store.get_task(target['id'])['status']=='open'
    assert store.get_task(target['id'])['title']=='Changed by another update'


def test_focus_unknown_calendar_write_recovers_without_repeating_the_move(command_scenario):
    from chronos.google_calendar import CalendarError
    s=command_scenario;client=s['client'];calendar=s['calendar'];db=s['db']
    original_exam=deepcopy(s['events']['fixed-exam'])
    assert post(client,8091,text='下午的時間都用來完成 task #'+str(s['aliases']['cp']['id'])).status_code==200
    review=db.get_task_action_state(calendar_replan.key(123))
    assert len(review['calendar_moves'])==3
    original=calendar.move_event.side_effect;failed=[False]
    async def lost_reply(event_id,body,etag):
        value=await original(event_id,body,etag)
        if not failed[0]:
            failed[0]=True
            raise CalendarError('Synthetic reply lost after the write succeeded')
        return value
    calendar.move_event.side_effect=lost_reply
    data='replan:allow:'+review['token'];message_id=review['message_id']
    assert post(client,8092,data=data,message_id=message_id).status_code==200
    assert db.get_task_action_state(calendar_replan.key(123))['status']=='creating'
    assert calendar.move_event.await_count==1
    assert post(client,8092,data=data,message_id=message_id).status_code==200
    assert calendar.move_event.await_count==1
    assert post(client,8093,data=data,message_id=message_id).status_code==200
    assert db.get_task_action_state(calendar_replan.key(123))['status']=='done'
    assert calendar.move_event.await_count==3
    assert s['events']['fixed-exam']==original_exam
    assert len(db.get_task_action_state('planning:holds')['slots'])==1
    assert post(client,8094,data=data,message_id=message_id).status_code==200
    assert calendar.move_event.await_count==3


@pytest.mark.parametrize('status',['pending','editing','creating','partial'])
def test_replan_cancellation_never_cancels_uncertain_writes(system,store,status):
    client,tasks,bot,cfg=system;cfg.enable_learning_calendar=True
    calendar=AsyncMock();calendar.configured=lambda:True
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        patch.setattr(main,'google_calendar',calendar)
        task=main.tasks.create('Keep')
        review={'status':status,'token':'a'*32,'chat_id':123,'message_id':500,
            'start':datetime.now(cfg.tz).isoformat(),'end':(datetime.now(cfg.tz)+timedelta(hours=1)).isoformat(),
            'calendar_moves':[],'expires_at':(datetime.now(cfg.tz)+timedelta(hours=1)).isoformat()}
        store.mutate_task_action_state(calendar_replan.key(123),lambda _:deepcopy(review))
        assert post(client,8021,text='/cancel').status_code==200
        saved=store.get_task_action_state(calendar_replan.key(123))
        assert saved['status']==('cancelled' if status in {'pending','editing'} else status)
        if status in {'pending','editing'}:
            assert post(client,8022,data='replan:allow:'+'a'*32).status_code==200
        assert store.get_task(task['id'])['status']=='open'
        calendar.move_event.assert_not_awaited();calendar.insert.assert_not_awaited();calendar.delete_event.assert_not_awaited()

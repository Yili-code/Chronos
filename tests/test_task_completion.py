import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import pytest

from chronos import main, task_completion
from chronos.google_calendar import CalendarError
from chronos.task_actions import task_fields
from chronos.intents import Intent
from test_learning_calendar import setup
from test_system import system
from test_intents import store, run, classify

TOKEN = 'a' * 32


def booked(setup):
    planner, task, calendar, bot = setup
    start = datetime.now(planner.settings.tz).replace(microsecond=0) + timedelta(days=3)
    state = {'token': TOKEN, 'status': 'scheduled', 'snapshot': task_fields(task),
        'start': start.isoformat(), 'end': (start+timedelta(hours=1)).isoformat(),
        'minutes':60, 'event_id':'future-event', 'message_id':50}
    planner.update(task['id'], lambda _: state)
    planner.db.mutate_task_action_state('learning:day:'+start.date().isoformat(), lambda _: {TOKEN:60})
    event = {'id':'future-event','etag':'v1','start':{'dateTime':start.isoformat()},
        'extendedProperties':{'private':{'chronosTask':str(task['id']),'chronosToken':TOKEN}}}
    calendar.event.return_value=event
    return planner, task, calendar, state, event


def test_completion_is_independent_of_future_calendar_and_duration_optional(setup):
    planner, task, calendar, state, _ = booked(setup)
    reply = task_completion.complete(planner, task, chat_id=123)
    assert planner.db.get_task(task['id'])['status']=='done'
    saved=planner.get(task['id'])
    assert saved['feedback']['actual_minutes'] is None and saved['feedback']['scope']=='task'
    assert saved['completion_cleanup']['status']=='offered'
    assert '取消原訂時段' in reply['messages'][0]['reply_markup']['inline_keyboard'][0][0]['text']
    assert not planner.db.get_task_action_state('learning:profile')
    calendar.event.assert_not_awaited()
    calendar.delete_event.assert_not_awaited()


def test_approximate_duration_followup_updates_once_without_recompleting(setup):
    planner, task, calendar, _, _ = booked(setup)
    task_completion.complete(planner, task, chat_id=123)
    action=task_completion.followup(planner,'可能三個小時吧，完整完成',123,datetime.now(planner.settings.tz))
    assert '180 分鐘' in action()
    assert planner.get(task['id'])['feedback']['actual_minutes']==180
    assert planner.get(task['id'])['feedback']['approximate']
    action()
    assert planner.db.get_task_action_state('learning:profile')['graphs']['samples']==1
    assert len(planner.db.list_open_tasks())==0
    calendar.insert.assert_not_awaited()


def test_optional_time_can_remain_unknown_and_unrelated_work_is_not_intercepted(setup):
    planner, task, _, _, _=booked(setup)
    task_completion.complete(planner,task,chat_id=123)
    assert task_completion.followup(planner,'明天花三小時讀新的報告',123,datetime.now(planner.settings.tz)) is None
    answer=task_completion.followup(planner,'沒記時間',123,datetime.now(planner.settings.tz))
    assert '先留空' in answer()
    assert planner.get(task['id'])['feedback']['actual_minutes'] is None
    assert not planner.db.get_task_action_state('learning:profile')


def test_future_cleanup_is_separate_guarded_and_replay_safe(setup):
    planner,task,calendar,state,_=booked(setup)
    task_completion.complete(planner,task,180,'約三個小時')
    assert '已取消' in asyncio.run(task_completion.cancel_future(planner,task['id'],TOKEN))
    calendar.delete_event.assert_awaited_once_with('future-event','v1')
    assert not planner.db.get_task_action_state('learning:day:'+state['start'][:10])
    assert '重複' in asyncio.run(task_completion.cancel_future(planner,task['id'],TOKEN))
    assert calendar.delete_event.await_count==1
    assert planner.get(task['id'])['feedback']['actual_minutes']==180


@pytest.mark.parametrize('change',['foreign','moved','started','shared','recurring','etag','unavailable'])
def test_cleanup_never_deletes_changed_or_unverified_event(setup,change):
    planner,task,calendar,state,event=booked(setup)
    task_completion.complete(planner,task,180)
    if change=='foreign':event['extendedProperties']['private']['chronosTask']='999'
    elif change=='moved':event['start']['dateTime']=(datetime.fromisoformat(state['start'])+timedelta(hours=1)).isoformat()
    elif change=='started':event['start']['dateTime']=(datetime.now(planner.settings.tz)-timedelta(hours=1)).isoformat()
    elif change=='shared':event['attendees']=[{'email':'other@example.com'}]
    elif change=='recurring':event['recurringEventId']='series'
    elif change=='etag':event.pop('etag')
    else:calendar.event.side_effect=CalendarError('Unavailable')
    assert '完成紀錄已保留' in asyncio.run(task_completion.cancel_future(planner,task['id'],TOKEN))
    calendar.delete_event.assert_not_awaited()
    assert planner.db.get_task(task['id'])['status']=='done'
    assert planner.db.get_task_action_state('learning:day:'+state['start'][:10])=={TOKEN:60}


def test_unknown_delete_result_reconciles_before_any_second_delete(setup):
    planner,task,calendar,_,_=booked(setup)
    task_completion.complete(planner,task,180)
    async def lost_ack(*args):
        calendar.event.return_value=None
        raise CalendarError('Uncertain outcome')
    calendar.delete_event.side_effect=lost_ack
    asyncio.run(task_completion.cancel_future(planner,task['id'],TOKEN))
    assert '已取消' in asyncio.run(task_completion.cancel_future(planner,task['id'],TOKEN))
    assert calendar.delete_event.await_count==1


def test_completion_checks_current_task_before_any_mutation(setup):
    planner,task,calendar,_,_=booked(setup)
    planner.db.edit_task_with_undo(task['id'],'Changed scope',None,None,{})
    assert '剛有變更' in task_completion.complete(planner,task,180)
    assert planner.db.get_task(task['id'])['status']=='open'
    calendar.delete_event.assert_not_awaited()


def test_screenshot_two_turns_never_call_model_or_future_session_guard(store,monkeypatch):
    monkeypatch.setattr(main.settings,'enable_learning_calendar',True)
    task=main.tasks.create('Database–systems Chapter 2')
    planner=main.learning_calendar()
    start=datetime.now(main.settings.tz)+timedelta(days=3)
    planner.update(task['id'],lambda _: {'status':'scheduled','token':TOKEN,'event_id':'future',
        'start':start.isoformat(),'end':(start+timedelta(hours=1)).isoformat(),'snapshot':task_fields(task),'minutes':60})
    mock=classify(monkeypatch,action='clarify',question='wrong question')
    first=run('剛複習完 DB ch2')
    assert '資料庫第 2 章已記錄完成' in first['messages'][0]['text']
    assert '完成、部分' not in first['messages'][0]['text']
    assert '(#' not in first['messages'][0]['text']
    second=run('可能三個小時吧，完整完成',update=2)
    assert '180 分鐘' in second['reply']
    assert planner.get(task['id'])['feedback']['actual_minutes']==180
    assert main.db.get_task(task['id'])['status']=='done'
    mock.assert_not_awaited()


def test_ambiguous_chapter_reference_never_completes_both(store,monkeypatch):
    monkeypatch.setattr(main.settings,'enable_learning_calendar',True)
    first=main.tasks.create('Database systems Chapter 2')
    second=main.tasks.create('Review Database systems Chapter 2')
    classify(monkeypatch,action='task_complete',task_ids=[first['id']])
    reply=run('剛複習完 DB ch2')
    assert '哪一項' in reply['reply']
    assert len(main.tasks.list_open())==2


def test_legacy_pending_completion_accepts_hours_without_reasking_outcome(store,monkeypatch):
    monkeypatch.setattr(main.settings,'enable_learning_calendar',True)
    task=main.tasks.create('Database systems Chapter 2')
    main.db.mutate_task_action_state('intent:clarify:123',lambda _: {'instruction':'剛複習完 DB ch2',
        'question':'這次花幾分鐘？完成還是部分完成？','candidate_ids':[task['id']],
        'snapshots':{str(task['id']):task_fields(task)},'parsed_intent':{'action':'plan_feedback'},
        'expires_at':(datetime.now(main.settings.tz)+timedelta(minutes=10)).isoformat(),'token':'old'})
    classify(monkeypatch,action='clarify',question='wrong')
    run('可能三個小時吧，完整完成')
    assert main.db.get_task(task['id'])['status']=='done'
    assert main.learning_calendar().get(task['id'])['feedback']['actual_minutes']==180


def test_task_completion_preserves_previous_session_history(setup):
    planner,task,_,state,_=booked(setup)
    planner.update(task['id'],lambda old:{**old,'status':'awaiting_resolution'})
    prior={'token':TOKEN,'status':'awaiting_resolution','feedback':{'outcome':'skipped','actual_minutes':0}}
    planner.db.mutate_task_action_state('learning:history:'+TOKEN,lambda _:prior)
    task_completion.complete(planner,task,180)
    assert planner.db.get_task_action_state('learning:history:'+TOKEN)==prior
    current=planner.get(task['id'])
    assert current['token']!=TOKEN
    assert current['feedback']['scope']=='task' and current['feedback']['outcome']=='done'


def test_completion_and_cleanup_follow_real_webhook_path(system,setup,monkeypatch):
    client,tasks,bot,config=system
    _,_,calendar,_=setup
    config.enable_learning_calendar=True
    monkeypatch.setattr(main,'google_calendar',calendar)
    task=tasks.create('Database systems Chapter 2')
    start=datetime.now(config.tz).replace(microsecond=0)+timedelta(days=3)
    planner=main.learning_calendar()
    planner.update(task['id'],lambda _: {'status':'scheduled','token':TOKEN,'event_id':'future',
        'start':start.isoformat(),'end':(start+timedelta(hours=1)).isoformat(),'snapshot':task_fields(task),'minutes':60})
    calendar.event.return_value={'etag':'current','start':{'dateTime':start.isoformat()},
        'extendedProperties':{'private':{'chronosTask':str(task['id']),'chronosToken':TOKEN}}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    for index,text in enumerate(['剛複習完 DB ch2','可能三個小時吧，完整完成'],99001):
        payload={'update_id':index,'message':{'message_id':index,'chat':{'id':123},'text':text}}
        assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    assert main.db.get_task(task['id'])['status']=='done'
    assert planner.get(task['id'])['feedback']['actual_minutes']==180
    payload={'update_id':99003,'callback_query':{'id':'cleanup','data':f"learn:completecancel:{task['id']}:{TOKEN}",
        'message':{'message_id':50,'chat':{'id':123}}}}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    calendar.delete_event.assert_awaited_once_with('future','current')
    assert planner.get(task['id'])['feedback']['actual_minutes']==180


def test_reading_chapter_does_not_implicitly_complete_its_report():
    items=[{'id':1,'title':'Database–systems Chapter 2'}, {'id':2,'title':'Database systems Chapter 2 report'}]
    assert task_completion.chapter_matches('剛複習完 DB ch2',items)==[1]
    assert not task_completion.explicit_done('剛複習完 DB ch2 一半')
    assert not task_completion.explicit_done('還沒複習完 DB ch2')

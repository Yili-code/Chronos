import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import pytest
from chronos import main, calendar_replan, task_focus, learning_reply
from chronos.learning_calendar import LearningCalendar
from chronos.task_actions import task_fields
from chronos.planning_holds import slots
from test_system import system
from test_intents import store
from test_learning_calendar import setup, NOW


@pytest.mark.parametrize('text', ['下午的時間都用來完成 task 2', '今天下午全部留給任務 2', '明天下午全都用來處理 task #42'])
def test_literal_focus(text):
    now = NOW.replace(hour=12, minute=28)
    request = task_focus.request(text, now)
    assert request and request['start'].hour == 13 and request['end'].hour == 18
    assert learning_reply.new_request(text)


@pytest.mark.parametrize('text', ['不要把下午的時間都用來完成 task 2', '下午的時間都用來完成 task 2 嗎？', 'task 2 完成了', '讀了兩小時'])
def test_unrelated_or_negated_text_is_not_focus(text):
    assert task_focus.request(text, NOW) is None


@pytest.mark.parametrize('stable', [False, True])
def test_webhook_resolves_task_without_duplicate_request_or_completion(system, store, monkeypatch, stable):
    client, _, bot, cfg = system
    cfg.enable_learning_calendar = True
    monkeypatch.setattr(LearningCalendar, 'tick', AsyncMock(return_value={}))
    calendar = AsyncMock(); calendar.configured = lambda: True
    monkeypatch.setattr(main, 'google_calendar', calendar)
    main.tasks.create('First task')
    target = main.tasks.create('Target preparation')
    planner = main.learning_calendar()
    planner.update(target['id'], lambda _: {'status':'scheduled','token':'existing','snapshot':task_fields(target)})
    ref = {'task_id':target['id'], 'token':'existing','mode':'feedback','expires_at':(datetime.now(cfg.tz)+timedelta(hours=2)).isoformat()}
    main.db.mutate_task_action_state('learning:edit-chat:123', lambda _: ref)
    classifier = AsyncMock(side_effect=AssertionError('literal request should not depend on model classification'))
    monkeypatch.setattr(main.ai, 'classify_intent', classifier)
    preview = AsyncMock(return_value={'messages':[{'text':'下午專注提案'}]})
    monkeypatch.setattr(calendar_replan, 'preview', preview)
    text = '下午的時間都用來完成 task ' + ('#'+str(target['id']) if stable else '2')
    payload = {'update_id':8901,'message':{'message_id':8901,'chat':{'id':123},'text':text}}
    headers = {'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    preview.assert_awaited_once(); classifier.assert_not_awaited()
    assert preview.await_args.kwargs['focus_task']['id'] == target['id']
    assert main.db.get_task(target['id'])['status'] == 'open'
    assert planner.get(target['id'])['status'] == 'scheduled'
    assert len(main.tasks.list_open()) == 2
    assert not main.db.get_task_action_state('learning:edit-chat:123')
    assert '下午專注提案' in bot.send_message.await_args.args[1]


def initialize(planner, target, calendar):
    now = NOW.replace(hour=12, minute=28)
    planner.db.edit_task(target['id'], target['title'], NOW+timedelta(days=6), target['project'], {})
    target = planner.db.get_task(target['id'])
    other = planner.db.create_task('Other reading', NOW+timedelta(days=6), None, NOW)
    events = {}
    for i, task in enumerate([target,other]):
        start = now.replace(hour=14+i, minute=0); end = start+timedelta(hours=1)
        token = 'session'+str(i); event_id = 'event'+str(i)
        planner.update(task['id'], lambda _, t=token, a=start, b=end, e=event_id: {'status':'scheduled','token':t,'event_id':e,'start':a.isoformat(),'end':b.isoformat(),'minutes':60})
        planner.db.mutate_task_action_state('learning:day:'+now.date().isoformat(), lambda old, t=token: {**(old or {}),t:60})
        events[event_id] = {'id':event_id,'etag':'v1','summary':task['title'],'_calendar_id':'primary','creator':{'email':'owner@example.com'},'organizer':{'self':True},'start':{'dateTime':start.isoformat()},'end':{'dateTime':end.isoformat()},'extendedProperties':{'private':{'chronosTask':str(task['id']),'chronosToken':token}}}
    calendar.agenda.side_effect = lambda *args: deepcopy(list(events.values()))
    calendar.event.side_effect = lambda i: deepcopy(events.get(i))
    async def move(i, body, etag):
        events[i].update(body); return deepcopy(events[i])
    calendar.move_event.side_effect = move
    return now, target, other, events


@pytest.mark.parametrize('change', [None,'target','new_fixed','retained_booking'])
def test_focus_preserves_target_and_only_moves_other_study_after_allow(setup, change):
    planner, target, calendar, _ = setup
    now, target, other, events = initialize(planner,target,calendar)
    window = (now.replace(hour=13,minute=0),now.replace(hour=18,minute=0))
    result = asyncio.run(calendar_replan.preview(planner,123,'下午的時間都用來完成 task 2',8902,now,focus_task=target,focus_window=window))
    assert isinstance(result,dict), result
    review = planner.db.get_task_action_state(calendar_replan.key(123))
    assert [m['task_id'] for m in review['calendar_moves']] == [other['id']]
    assert not slots(planner.db)
    calendar.move_event.assert_not_awaited()
    calendar_replan.bind(planner.db,123,50,result['messages'][0])
    if change == 'target': planner.db.edit_task(target['id'],'Changed',None,None,{})
    if change == 'new_fixed': events['meeting'] = {'id':'meeting','summary':'Meeting','_calendar_id':'primary','start':{'dateTime':window[0].isoformat()},'end':{'dateTime':window[1].isoformat()}}
    if change == 'retained_booking': events['event0']['start']['dateTime'] = window[0].isoformat()
    reply = asyncio.run(calendar_replan.callback(planner,123,50,review['token'],'allow',now))
    if change:
        calendar.move_event.assert_not_awaited(); assert not slots(planner.db)
    else:
        assert isinstance(reply,dict), reply
        assert '下午已保留' in reply['messages'][0]['text']
        assert calendar.move_event.await_count == 1
        assert calendar.move_event.await_args.args[0] == 'event1'
        assert planner.get(target['id'])['start'][11:16] == '14:00'
        assert planner.db.get_task(target['id'])['status'] == 'open'
        assert slots(planner.db)[0]['title'] == target['title']
    calendar.insert.assert_not_awaited()

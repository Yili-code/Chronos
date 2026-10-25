"""Synthetic live-output contracts replayed through the real Telegram route."""
import copy
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from chronos import main, learning_calendar
from chronos.ai import ScheduleEditOutput
from chronos.db import Database
from chronos.intents import Intent
from chronos.learning_calendar import LearningCalendar
from chronos.settings import Settings
from chronos.task_actions import task_fields
from chronos.tasks import TaskService
from test_firestore import database as firestore_database

NOW = datetime.fromisoformat('2026-10-24T09:00:00+08:00')
DATA = Path(__file__).parent / 'fixtures' / 'natural_schedule_edits.json'
CASES = json.loads(DATA.read_text(encoding='utf-8')) if DATA.exists() else []


@pytest.fixture(params=['sqlite','firestore'])
def audit(request, tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return NOW.astimezone(tz)
    monkeypatch.setattr(learning_calendar,'datetime',Clock)
    monkeypatch.setattr(main,'datetime',Clock)
    cfg=Settings(_env_file=None,enable_learning_calendar=True,learning_auto_accept=True,
        learning_enabled_since=NOW.isoformat(),enable_internal_scheduler=False,
        telegram_chat_id=123,telegram_webhook_secret='test-hook',calendar_account='owner@example.com')
    db=firestore_database(monkeypatch) if request.param=='firestore' else Database(tmp_path/'audit.db')
    db.initialize()
    task=db.create_task('Database report',datetime.fromisoformat('2026-11-01T18:00:00+08:00'),'database',NOW,{})
    other=db.create_task('Algorithms review',None,'algorithms',NOW,{})
    calendar=AsyncMock();calendar.configured=lambda:True
    bot=AsyncMock();bot.send_message.return_value={'ok':True,'result':{'message_id':50}}
    bot.request.return_value={'ok':True}
    planner=LearningCalendar(db,calendar,bot,cfg,main.ai)
    state={'status':'scheduled','token':'a'*32,'event_id':'owned','minutes':60,
        'start':'2026-10-25T10:00:00+08:00','end':'2026-10-25T11:00:00+08:00',
        'snapshot':task_fields(task),'message_id':40}
    planner.update(task['id'],lambda old:state)
    db.mutate_task_action_state('learning:day:2026-10-25',lambda old:{state['token']:60})
    event={'id':'owned','etag':'old','_calendar_id':'primary',
        'creator':{'email':cfg.calendar_account},'organizer':{'self':True},
        'start':{'dateTime':state['start']},'end':{'dateTime':state['end']},
        'extendedProperties':{'private':{'chronosTask':str(task['id']),'chronosToken':state['token']}}}
    calendar.event.return_value=event
    calendar.agenda.return_value=[event]
    calendar.busy.return_value=[]
    async def move(event_id, body, etag):
        assert event_id=='owned' and etag==event['etag']
        event.update(copy.deepcopy(body));event['etag']='new'
        return event
    calendar.move_event.side_effect=move
    for name,value in [('settings',cfg),('db',db),('tasks',TaskService(db,cfg.tz)),('telegram',bot),('google_calendar',calendar)]:
        monkeypatch.setattr(main,name,value)
    monkeypatch.setattr(main,'learning_calendar',lambda:planner)
    return db,task,other,planner,calendar,bot


@pytest.mark.parametrize('record',CASES,ids=lambda r:'case'+str(r['case']['id']))
def test_natural_edit_contract(audit,monkeypatch,record):
    db,task,other,planner,calendar,bot=audit
    case=record['case'];raw=copy.deepcopy(record['intent'])
    raw['task_ids']=[{701:task['id'],702:other['id']}[i] for i in raw['task_ids']]
    classifier=AsyncMock(return_value=Intent.model_validate(raw))
    monkeypatch.setattr(main.ai,'classify_intent',classifier)
    if record.get('edit'):
        editor=AsyncMock(return_value=ScheduleEditOutput.model_validate(record['edit']))
    else:editor=AsyncMock(side_effect=AssertionError('Unexpected schedule parser'))
    monkeypatch.setattr(main.ai,'parse_schedule_edit',editor)
    if case.get('conflict'):
        calendar.agenda.return_value=[calendar.event.return_value,{'id':'busy','_calendar_id':'primary',
            'start':{'dateTime':case['start']},'end':{'dateTime':'2026-10-25T17:00:00+08:00'}}]
    before=copy.deepcopy(planner.get(task['id']))
    client=TestClient(main.app,raise_server_exceptions=True)
    payload={'update_id':70000+case['id'],'message':{'message_id':100+case['id'],'chat':{'id':123},'text':case['text']}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    for _ in range(2):
        response=client.post('/telegram/webhook',json=payload,headers=headers)
        assert response.status_code==200,response.text
    current=planner.get(task['id'])
    if case.get('clarify') or case.get('ambiguous') or case.get('conflict'):
        calendar.move_event.assert_not_awaited()
        assert current==before
        if case.get('ambiguous'):
            assert raw['action']=='clarify' or raw.get('question')
        elif case.get('clarify'):
            assert not record.get('edit',{}).get('actionable',False) or raw['action']=='clarify'
    else:
        calendar.move_event.assert_awaited_once()
        assert current['minutes']==case['minutes']
        if case.get('start'):assert current['start']==case['start']
        assert datetime.fromisoformat(current['end'])-datetime.fromisoformat(current['start'])==timedelta(minutes=case['minutes'])
        if case.get('window_end'):assert datetime.fromisoformat(current['end'])<=datetime.fromisoformat(case['window_end'])
        assert current['status']=='scheduled' and 'pending_reschedule' not in current
    assert len(db.list_open_tasks())==2
    assert task_fields(db.list_open_tasks()[0])==task_fields(task)
    calendar.insert.assert_not_awaited()
    classifier.assert_awaited_once()


@pytest.mark.parametrize('start,end',[('2026-10-25T06:00:00+08:00',None),
    ('2026-10-25T06:00:00', '2026-10-25T12:00:00'),
    ('2026-10-25T12:00:00+08:00','2026-10-25T06:00:00+08:00'),
    ('2026-10-25T06:00:00+08:00','2026-11-25T12:00:00+08:00')])
def test_invalid_windows_never_move_event(audit,monkeypatch,start,end):
    import asyncio
    db,task,other,planner,calendar,bot=audit
    monkeypatch.setattr(main.ai,'parse_schedule_edit',AsyncMock(return_value=ScheduleEditOutput(
        actionable=True,minutes=60,timing='window',start=start,window_end=end,progress_note='')))
    before=planner.get(task['id'])
    reply=asyncio.run(planner.revise(task['id'],before['token'],'Move to tomorrow morning',NOW))
    assert 'unchanged' in reply
    calendar.move_event.assert_not_awaited()
    assert planner.get(task['id'])==before


def test_window_with_no_capacity_does_not_escape_requested_day(audit,monkeypatch):
    import asyncio
    db,task,other,planner,calendar,bot=audit
    monkeypatch.setattr(main.ai,'parse_schedule_edit',AsyncMock(return_value=ScheduleEditOutput(
        actionable=True,minutes=60,timing='window',start='2026-10-25T06:00:00+08:00',
        window_end='2026-10-25T12:00:00+08:00',progress_note='')))
    calendar.agenda.return_value=[{'id':'busy','_calendar_id':'primary',
        'start':{'dateTime':'2026-10-25T06:00:00+08:00'},'end':{'dateTime':'2026-10-25T12:00:00+08:00'}}]
    before=planner.get(task['id'])
    reply=asyncio.run(planner.revise(task['id'],before['token'],'Move to tomorrow morning',NOW))
    assert 'unchanged' in reply
    calendar.move_event.assert_not_awaited()
    assert planner.get(task['id'])==before


def test_natural_edit_rejects_task_scope_changed_during_classification(audit, monkeypatch):
    from datetime import datetime
    from fastapi.testclient import TestClient
    from chronos import main
    from chronos.intents import Intent
    from unittest.mock import AsyncMock
    db, task, other, planner, calendar, bot = audit
    before = planner.get(task['id'])
    async def classify(*args, **kwargs):
        db.edit_task(task['id'], 'Different report scope', datetime.fromisoformat(task['due_at']), task['project'], task['timing'])
        return Intent(action='plan_edit', task_ids=[task['id']], instruction='10/31 14:30 30')
    monkeypatch.setattr(main.ai, 'classify_intent', AsyncMock(side_effect=classify))
    response = TestClient(main.app).post('/telegram/webhook',
        headers={'X-Telegram-Bot-Api-Secret-Token': 'test-hook'},
        json={'update_id': 88001, 'message': {'message_id': 110, 'chat': {'id': 123},
              'text': '把資料庫報告改到 10/31 下午兩點半，半小時'}})
    assert response.status_code == 200
    calendar.move_event.assert_not_awaited()
    assert planner.get(task['id']) == before
    assert db.get_task(task['id'])['title'] == 'Different report scope'
    assert db.get_task(task['id'])['status'] == 'open'

@pytest.mark.parametrize('text,question', [
    ('資料庫那個報高晚點用，30 還是 90 分都可以？', '要安排 30 還是 90 分鐘？'),
    ('move DB report tmrw afternon，但開始時間不要動', '要改開始時間，還是保留原時段？'),
    ('明天早上排兩小時，但只能用一小時', '要縮短時長，還是擴大時間窗口？'),
    ('把資料庫跟演算法都調一下', '這兩項各要怎麼調整？'),
    ('就剛剛那個，差不多就好', '要調整哪一項，以及哪個時間？'),
])
def test_uncertain_provider_result_preserves_question_and_has_no_side_effects(audit, monkeypatch, text, question):
    from copy import deepcopy
    from fastapi.testclient import TestClient
    from chronos import main
    from chronos.intents import Intent
    from unittest.mock import AsyncMock
    db, task, other, planner, calendar, bot = audit
    original = deepcopy(db.list_open_tasks())
    state = deepcopy(planner.get(task['id']))
    # The provider reports unresolved meaning even though it populated an action.
    # This tests the execution boundary, not the live model's classification skill.
    monkeypatch.setattr(main.ai, 'classify_intent', AsyncMock(return_value=Intent(
        action='plan_edit', task_ids=[task['id']], instruction=text, question=question)))
    response = TestClient(main.app).post('/telegram/webhook',
        headers={'X-Telegram-Bot-Api-Secret-Token': 'test-hook'},
        json={'update_id': 88002, 'message': {'message_id': 111, 'chat': {'id': 123}, 'text': text}})
    assert response.status_code == 200
    calendar.move_event.assert_not_awaited()
    calendar.insert.assert_not_awaited()
    calendar.delete_event.assert_not_awaited()
    assert db.list_open_tasks() == original
    assert planner.get(task['id']) == state
    receipt = db.get_update(88002)
    assert question in '\n'.join(m['text'] for m in receipt['messages'])


def test_booked_edit_replay_after_local_commit_before_webhook_receipt_does_not_patch_twice(audit, monkeypatch):
    from fastapi.testclient import TestClient
    from chronos import main
    from chronos.intents import Intent
    from unittest.mock import AsyncMock
    db, task, other, planner, calendar, bot = audit
    monkeypatch.setattr(main.ai, 'classify_intent', AsyncMock(return_value=Intent(
        action='plan_edit', task_ids=[task['id']], instruction='10/31 14:30 30')))
    process = db.process_update
    interrupted = False
    def fail_receipt_once(update_id, action):
        nonlocal interrupted
        if update_id == 88003 and not interrupted:
            interrupted = True
            raise RuntimeError('Interrupted after local booking commit, before webhook receipt')
        return process(update_id, action)
    monkeypatch.setattr(db, 'process_update', fail_receipt_once)
    client = TestClient(main.app, raise_server_exceptions=False)
    payload = {'update_id': 88003, 'message': {'message_id': 112, 'chat': {'id': 123},
               'text': '把資料庫報告改到 10/31 下午兩點半，半小時'}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 500
    saved = planner.get(task['id'])
    assert saved['start'] == '2026-10-31T14:30:00+08:00'
    assert 'pending_reschedule' not in saved
    assert db.get_update(88003) is None
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    calendar.move_event.assert_awaited_once()
    assert planner.get(task['id']) == saved
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    calendar.move_event.assert_awaited_once()


def test_edit_checkpoint_failure_rolls_back_local_receipt_and_recovers_without_patch(audit, monkeypatch):
    from fastapi.testclient import TestClient
    from chronos import main
    from chronos.intents import Intent
    from unittest.mock import AsyncMock
    db, task, other, planner, calendar, bot = audit
    monkeypatch.setattr(main.ai, 'classify_intent', AsyncMock(return_value=Intent(
        action='plan_edit', task_ids=[task['id']], instruction='10/31 14:30 30')))
    save = db._save_task_action_state
    interrupted = False
    def fail_checkpoint_once(key, value):
        nonlocal interrupted
        if key == 'learning:edit-receipt:88004' and value.get('reply') and not interrupted:
            interrupted = True
            raise RuntimeError('Checkpoint persistence failed')
        return save(key, value)
    monkeypatch.setattr(db, '_save_task_action_state', fail_checkpoint_once)
    original = planner.get(task['id'])
    client = TestClient(main.app, raise_server_exceptions=False)
    payload = {'update_id': 88004, 'message': {'message_id': 113, 'chat': {'id': 123}, 'text': '把報告改到 10/31 下午兩點半，半小時'}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 500
    saved = planner.get(task['id'])
    assert saved['start'] == original['start'] and saved.get('pending_reschedule')
    assert db.get_task_action_state('learning:edit-receipt:88004') == {'task_id':task['id'],'token':original['token']}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    calendar.move_event.assert_awaited_once()
    assert planner.get(task['id'])['start'] == '2026-10-31T14:30:00+08:00'
    assert 'recovered' in db.get_update(88004)['reply']


def test_recovery_reply_checkpoint_cannot_turn_into_a_new_move_after_restart(audit, monkeypatch):
    from fastapi.testclient import TestClient
    from chronos import main
    from chronos.intents import Intent
    from chronos.google_calendar import CalendarError
    from unittest.mock import AsyncMock
    db, task, other, planner, calendar, bot = audit
    async def classify(text, *args, **kwargs):
        return Intent(action='plan_edit', task_ids=[task['id']], instruction='10/31 14:30 30' if text == 'first edit' else '10/31 16:00 60')
    monkeypatch.setattr(main.ai, 'classify_intent', AsyncMock(side_effect=classify))
    move = calendar.move_event.side_effect
    async def lost(*args):
        await move(*args)
        raise CalendarError('Lost PATCH response')
    calendar.move_event.side_effect = lost
    client = TestClient(main.app, raise_server_exceptions=False)
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    def payload(i, text): return {'update_id': i, 'message': {'message_id': i, 'chat': {'id': 123}, 'text': text}}
    assert client.post('/telegram/webhook', headers=headers, json=payload(88005, 'first edit')).status_code == 200
    assert planner.get(task['id']).get('pending_reschedule')
    process = db.process_update
    interrupted = False
    def fail_once(i, action):
        nonlocal interrupted
        if i == 88006 and not interrupted:
            interrupted = True
            raise RuntimeError('Interrupted before recovery reply receipt')
        return process(i, action)
    monkeypatch.setattr(db, 'process_update', fail_once)
    calendar.move_event.side_effect = move
    assert client.post('/telegram/webhook', headers=headers, json=payload(88006, 'different edit')).status_code == 500
    assert client.post('/telegram/webhook', headers=headers, json=payload(88006, 'different edit')).status_code == 200
    assert 'recovered' in db.get_update(88006)['reply']
    assert planner.get(task['id'])['start'] == '2026-10-31T14:30:00+08:00'
    calendar.move_event.assert_awaited_once()


@pytest.mark.parametrize('after_write',[False,True])
@pytest.mark.parametrize('busy_on_replay',[False,True])
def test_interrupted_edit_replay_cannot_reclassify_to_another_task(audit, monkeypatch, after_write, busy_on_replay):
    import asyncio
    from copy import deepcopy
    from datetime import timedelta
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    from chronos import main
    from chronos.intents import Intent
    db, task, other, planner, calendar, bot = audit
    original_state=deepcopy(planner.get(task['id']))
    other_state={**deepcopy(original_state),'token':'b'*32,'event_id':'other-event','snapshot':task_fields(other)}
    planner.update(other['id'],lambda old:other_state)
    first=deepcopy(calendar.event.return_value)
    second=deepcopy(first)
    second.update(id='other-event',start={'dateTime':'2026-10-25T13:00:00+08:00'},end={'dateTime':'2026-10-25T14:00:00+08:00'})
    second['extendedProperties']['private'].update(chronosTask=str(other['id']),chronosToken='b'*32)
    events={'owned':first,'other-event':second}
    other_before=deepcopy(second)
    calendar.event.side_effect=lambda i:deepcopy(events.get(i))
    calendar.agenda.side_effect=lambda *args:list(deepcopy(events).values())
    calls=0
    async def move(i,body,etag):
        nonlocal calls
        calls+=1
        if calls==1 and not after_write: raise RuntimeError('Process stopped before PATCH effect')
        events[i].update(deepcopy(body));events[i]['etag']='new'
        if calls==1: raise RuntimeError('Process stopped after PATCH effect')
        return deepcopy(events[i])
    calendar.move_event.side_effect=move
    classifier=AsyncMock(side_effect=[Intent(action='plan_edit',task_ids=[task['id']],instruction='10/31 14:30 30'),
                                      Intent(action='plan_edit',task_ids=[other['id']],instruction='10/31 14:30 30')])
    monkeypatch.setattr(main.ai,'classify_intent',classifier)
    client=TestClient(main.app,raise_server_exceptions=False)
    payload={'update_id':88007,'message':{'message_id':120,'chat':{'id':123},'text':'把資料庫報告改到 10/31 下午兩點半，半小時'}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==500
    assert planner.get(task['id']).get('pending_reschedule')
    assert db.get_update(88007) is None
    if busy_on_replay:
        db.mutate_task_action_state('learning:tick',lambda old:{'claim':'active-writer',
            'until':(NOW+timedelta(minutes=10)).isoformat()})
        assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==503
        assert db.get_update(88007) is None
        classifier.assert_awaited_once()
        assert calendar.move_event.await_count==1
        db.mutate_task_action_state('learning:tick',lambda old:{**old,'until':NOW.isoformat()})
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    assert events['other-event']==other_before
    assert planner.get(task['id'])['start']=='2026-10-31T14:30:00+08:00'
    assert planner.get(other['id'])==other_state
    classifier.assert_awaited_once()
    assert calendar.move_event.await_count==(1 if after_write else 2)

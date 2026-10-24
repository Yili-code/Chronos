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

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from chronos import main, intents
from chronos.google_calendar import CalendarError
from test_system import system


@pytest.mark.parametrize('command', ['/brief','/week','/time'])
@pytest.mark.parametrize('unavailable',[False,True])
def test_report_webhook_receipt_replay_is_read_only(system,monkeypatch,command,unavailable):
    client,tasks,bot,cfg=system
    cal=AsyncMock();cal.agenda.return_value=[]
    if unavailable:cal.agenda.side_effect=CalendarError('provider failed')
    monkeypatch.setattr(main,'google_calendar',cal)
    tasks.create('Unchanged task')
    before=tasks.list_open()
    payload={'update_id':51001,'message':{'message_id':900,'chat':{'id':123},'text':command}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    calls=bot.send_message.await_count
    assert calls>0
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    assert bot.send_message.await_count==calls and cal.agenda.await_count==1
    assert tasks.list_open()==before and not cal.insert.called and not cal.move_event.called
    if unavailable:assert 'unavailable or incomplete' in bot.send_message.await_args.args[1]


@pytest.mark.parametrize('action,text',[
    ('briefing','整理今天行程和到期任務'),('weekly','給我下週規劃建議，先不要改行程'),
    ('time_usage','分析過去七個完整日的時間使用，不要把計畫當實際')])
def test_natural_report_routes_through_real_handler(system,monkeypatch,action,text):
    client,tasks,bot,cfg=system
    cal=AsyncMock();cal.agenda.return_value=[]
    monkeypatch.setattr(main,'google_calendar',cal)
    classifier=AsyncMock(return_value=intents.Intent(action=action))
    monkeypatch.setattr(main.ai,'classify_intent',classifier)
    payload={'update_id':51002,'message':{'chat':{'id':123},'text':text}}
    assert client.post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json=payload).status_code==200
    classifier.assert_awaited_once()
    assert classifier.await_args.args[0]==text
    cal.agenda.assert_awaited_once()
    assert tasks.list_open()==[] and not cal.insert.called


@pytest.mark.parametrize('kind',['brief','week','time'])
def test_operator_preview_auth_and_no_delivery(system,monkeypatch,kind):
    client,tasks,bot,cfg=system
    cal=AsyncMock();cal.agenda.return_value=[]
    monkeypatch.setattr(main,'google_calendar',cal)
    endpoint='/internal/reports/'+kind
    assert client.get(endpoint).status_code==403
    assert client.get(endpoint,headers={'X-Chronos-Scheduler-Secret':'wrong'}).status_code==403
    cal.agenda.assert_not_awaited()
    headers={'X-Chronos-Scheduler-Secret':'test-scheduler'}
    response=client.get(endpoint,headers=headers)
    assert response.status_code==200 and response.json()['messages']
    assert response.headers['cache-control']=='no-store'
    bot.send_message.assert_not_awaited()
    cal.insert.assert_not_awaited()
    assert tasks.list_open()==[]
    cal.agenda.side_effect=CalendarError('failed')
    assert client.get(endpoint,headers=headers).status_code==503
    assert client.get('/internal/reports/unknown',headers=headers).status_code==404

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from chronos import main, reset_conversation as flow, study_reset as core, event_reset
from chronos.ai import ExternalAI
from chronos.tasks import TaskService
from test_learning_calendar import setup, NOW
from test_ai import config, mock_provider
from test_intents import store, run


def prepare(planner,text,update=1,now=NOW,date=None):
    return asyncio.run(flow.prepare(planner,123,text,update,now,date))


def test_broad_reset_question_and_named_answer_retain_owner_instruction(setup,monkeypatch):
    planner,*_=setup
    execute=AsyncMock(return_value='reset prepared')
    monkeypatch.setattr(event_reset,'run',execute)
    assert prepare(planner,'重排我所有的行程')==flow.QUESTION
    planner.calendar.insert.assert_not_awaited()
    assert prepare(planner,'study reset',2,NOW+timedelta(minutes=18))=='reset prepared'
    assert execute.call_args.args[2]=='study'
    assert '重排我所有的行程' in execute.call_args.args[3]
    assert not planner.db.get_task_action_state('intent:clarify:123')


@pytest.mark.parametrize('answer',['study','讀書','study_reset','STUDY RESET'])
def test_type_answer_does_not_depend_on_another_classifier_call(setup,monkeypatch,answer):
    planner,*_=setup
    execute=AsyncMock(return_value='selected')
    monkeypatch.setattr(event_reset,'run',execute)
    prepare(planner,'重排我所有的行程')
    assert prepare(planner,answer,2)=='selected'
    assert execute.call_args.args[2]=='study'


def test_waiting_message_reports_failed_preflight_without_triggering_rebuild(setup):
    planner,*_=setup
    planner.db.mutate_task_action_state(core.KEY,lambda old:{'status':'planning','token':'request','event_type':'study'})
    planner.db.mutate_task_action_state('learning:tick',lambda old:{'claim':'old','until':NOW.isoformat()})
    reply=prepare(planner,'I still waiting bro')
    assert '中斷' in reply
    assert planner.db.get_task_action_state(core.KEY)['status']=='failed'
    planner.calendar.delete_event.assert_not_awaited()
    planner.calendar.insert.assert_not_awaited()


def test_active_planning_status_does_not_interrupt_its_lease(setup):
    planner,*_=setup
    planner.db.mutate_task_action_state(core.KEY,lambda old:{'status':'planning','token':'request'})
    planner.db.mutate_task_action_state('learning:tick',lambda old:{'claim':'active','until':(NOW+timedelta(minutes=1)).isoformat()})
    assert '正在規劃' in prepare(planner,'still waiting')
    assert planner.db.get_task_action_state(core.KEY)['status']=='planning'


@pytest.mark.parametrize('status',['applying','partial'])
def test_expired_delivery_and_recovery_never_discard_external_progress(setup,status):
    planner,*_=setup
    state={'status':status,'token':'request','event_type':'study','deleted':['old'],'inserted':[]}
    planner.db.mutate_task_action_state(core.KEY,lambda old:state)
    assert prepare(planner,'study reset',date=int((NOW-timedelta(minutes=20)).timestamp()))==flow.EXPIRED
    assert planner.db.get_task_action_state(core.KEY)==state
    planner.calendar.delete_event.assert_not_awaited()


def test_old_failed_delivery_cannot_become_a_new_calendar_write(setup,monkeypatch):
    planner,*_=setup
    planner.db.mutate_task_action_state(core.KEY,lambda old:{'status':'planning','token':'old','event_type':'study'})
    planner.db.mutate_task_action_state('learning:tick',lambda old:{'claim':'old','until':NOW.isoformat()})
    execute=AsyncMock(return_value='fresh only')
    monkeypatch.setattr(event_reset,'run',execute)
    assert prepare(planner,'study reset',date=int((NOW-timedelta(minutes=11)).timestamp()))==flow.EXPIRED
    execute.assert_not_awaited()
    assert planner.db.get_task_action_state(core.KEY)['status']=='failed'
    assert prepare(planner,'study reset',2,date=int(NOW.timestamp()))=='fresh only'
    execute.assert_awaited_once()


def test_background_worker_releases_only_abandoned_preflight(setup):
    planner,*_=setup
    planner.db.mutate_task_action_state(core.KEY,lambda old:{'status':'planning','token':'abandoned','event_type':'study'})
    planner.db.mutate_task_action_state('learning:tick',lambda old:{'claim':'old','until':NOW.isoformat()})
    asyncio.run(planner.tick(NOW))
    assert planner.db.get_task_action_state(core.KEY)['status']=='failed'


@pytest.mark.parametrize('text',['study reset','重排所有會議'])
def test_actual_webhook_acknowledges_old_retry_without_running_rebuild(setup,monkeypatch,text):
    from chronos.intents import Intent
    planner,*_=setup
    class Clock(flow.datetime):
        @classmethod
        def now(cls,tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(flow,'datetime',Clock)
    monkeypatch.setattr(main,'settings',planner.settings)
    monkeypatch.setattr(main,'db',planner.db)
    monkeypatch.setattr(main,'tasks',TaskService(planner.db,planner.settings.tz))
    classifier=AsyncMock()
    classifier.classify_intent.return_value=Intent(action='event_reset',reset_type='meeting')
    monkeypatch.setattr(main,'ai',classifier)
    monkeypatch.setattr(main,'learning_calendar',lambda:planner)
    monkeypatch.setattr(main,'telegram',planner.telegram)
    execute=AsyncMock()
    monkeypatch.setattr(event_reset,'run',execute)
    planner.db.mutate_task_action_state(core.KEY,lambda old:{'status':'planning','token':'old','event_type':'study'})
    planner.db.mutate_task_action_state('learning:tick',lambda old:{'claim':'old','until':NOW.isoformat()})
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            body={'update_id':90,'message':{'message_id':90,'chat':{'id':123},'text':text,'date':int((NOW-timedelta(minutes=20)).timestamp())}}
            assert (await client.post('/telegram/webhook',json=body)).status_code==200
            assert (await client.post('/telegram/webhook',json=body)).status_code==200
    asyncio.run(request())
    assert planner.db.get_update(90)['reply']==flow.EXPIRED
    assert planner.db.get_update(90)['delivered']
    execute.assert_not_awaited()
    planner.calendar.insert.assert_not_awaited()
    planner.calendar.delete_event.assert_not_awaited()


@pytest.mark.parametrize('text',['/eventreset','/eventreset meeting','/eventreset@ChronosBot study','/eventreset help'])
def test_removed_command_is_a_noop_under_all_scopes(store,monkeypatch,text):
    execute=AsyncMock()
    monkeypatch.setattr(event_reset,'run',execute)
    assert run(text)['reply']==flow.REMOVED
    assert not store.list_open_tasks()
    execute.assert_not_awaited()


def test_provider_transport_is_exercised_by_real_study_reply_webhook(setup,monkeypatch):
    import json
    planner,*_=setup
    class Clock(flow.datetime):
        @classmethod
        def now(cls,tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(flow,'datetime',Clock)
    monkeypatch.setattr(main,'settings',planner.settings)
    monkeypatch.setattr(main,'db',planner.db)
    monkeypatch.setattr(main,'tasks',TaskService(planner.db,planner.settings.tz))
    monkeypatch.setattr(main,'learning_calendar',lambda:planner)
    monkeypatch.setattr(main,'telegram',planner.telegram)
    planner.calendar.agenda.return_value=[]
    planner.ai=ExternalAI(config())
    calls=[]
    def provider(request):
        calls.append(request)
        result={'actionable':False,'question':'是否包含過期行程？'}
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(result)}]}}]})
    native_client=httpx.AsyncClient
    mock_provider(monkeypatch,provider)
    async def replay():
        async with native_client(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            message={'message_id':1,'chat':{'id':123},'text':'study reset','date':int(NOW.timestamp())}
            first=await client.post('/telegram/webhook',json={'update_id':1,'message':message})
            assert first.status_code==200,first.text
            assert (await client.post('/telegram/webhook',json={'update_id':1,'message':message})).status_code==200
    asyncio.run(replay())
    assert len(calls)==1
    assert planner.db.get_task_action_state(core.KEY)['status']=='question'
    planner.calendar.delete_event.assert_not_awaited()
    planner.calendar.insert.assert_not_awaited()


def test_old_ordinary_task_with_reset_word_is_not_dropped(store,monkeypatch):
    from test_intents import classify
    monkeypatch.setattr(main.settings,'enable_learning_calendar',True)
    classify(monkeypatch,action='task_create')
    old=int((flow.datetime.now(main.settings.tz)-timedelta(minutes=20)).timestamp())
    action=asyncio.run(main.prepare_message('Add a task to reset my password',update_id=200,chat_id=123,request_date=old))
    store.process_update(200,action)
    assert len(store.list_open_tasks())==1


def test_classified_old_generic_reset_is_still_expired(store,monkeypatch):
    from test_intents import classify
    monkeypatch.setattr(main.settings,'enable_learning_calendar',True)
    classify(monkeypatch,action='event_reset',reset_type='workout')
    execute=AsyncMock()
    monkeypatch.setattr(event_reset,'run',execute)
    old=int((flow.datetime.now(main.settings.tz)-timedelta(minutes=20)).timestamp())
    action=asyncio.run(main.prepare_message('把所有健身活動重新安排',update_id=201,chat_id=123,request_date=old))
    assert store.process_update(201,action)['reply']==flow.EXPIRED
    execute.assert_not_awaited()

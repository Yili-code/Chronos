import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import json

import httpx
import pytest

from chronos import sequence_replan as module, calendar_replan, main
from chronos.ai import SequenceReplanOutput, ExternalAI
from chronos.db import Database
from chronos.google_calendar import CalendarError, GoogleCalendar
from chronos.learning_calendar import LearningCalendar
from chronos.settings import Settings
from chronos.task_actions import task_fields
from chronos.tasks import TaskService
from test_firestore import database as firestore_database

NOW = datetime.fromisoformat('2026-10-24T23:10:00+08:00')


@pytest.fixture(params=['sqlite','firestore'])
def group(request,tmp_path,monkeypatch):
    db = firestore_database(monkeypatch) if request.param == 'firestore' else Database(tmp_path/'sequence.db')
    db.initialize()
    cfg=Settings(_env_file=None,enable_learning_calendar=True,learning_enabled_since=NOW.isoformat(),
                 calendar_account='owner@example.com',telegram_chat_id=123,learning_daily_minutes=600)
    db.mutate_task_action_state('learning:course-calendar-policy',lambda old:{'first_day':'2020-01-01','last_regular_day':'2020-01-02','excluded_dates':[]})
    ai,calendar,bot=AsyncMock(),AsyncMock(),AsyncMock()
    planner=LearningCalendar(db,calendar,bot,cfg,ai=ai)
    events={}
    calendar.configured=lambda:True
    calendar.search_events.side_effect=lambda query:list(deepcopy(events).values())
    calendar.event.side_effect=lambda i:deepcopy(events.get(i))
    calendar.agenda.side_effect=lambda start,end:[deepcopy(e) for e in events.values() if module.bounds(e,cfg.tz)[0] < end and module.bounds(e,cfg.tz)[1] > start]
    async def patch(i,body,etag):
        if events[i]['etag'] != etag:
            raise CalendarError('HTTP 412: external version changed')
        events[i].update(body)
        events[i]['etag']='v2'
        return deepcopy(events[i])
    calendar.move_event.side_effect=patch
    ai.parse_sequence_replan.return_value=SequenceReplanOutput(actionable=True,scope='ISPM',ordinals=[1,2,3])
    def add(number,*,day=25,hour=9,minutes=60,subject='ISPM',linked=False):
        i=f'{subject}-{number}'
        start=NOW.replace(day=day,hour=hour,minute=0,second=0)
        props={}
        if linked:
            task=db.create_task(f'Study {subject} Chapter {number}',None,subject,NOW,{})
            props={'chronosTask':str(task['id']),'chronosToken':i}
            planner.update(task['id'],lambda old:{'status':'scheduled','token':i,'event_id':i,'minutes':minutes,
                'start':start.isoformat(),'end':(start+timedelta(minutes=minutes)).isoformat(),'snapshot':task_fields(task)})
            db.mutate_task_action_state('learning:day:'+start.date().isoformat(),lambda old:{**(old or {}),i:minutes})
        events[i]={'id':i,'etag':'v1','summary':f'Study {subject} Chapter {number}',
            'description':'Original notes <keep>','location':'Library','attachments':[{'fileId':'material'}],
            '_calendar_id':'primary','creator':{'email':'owner@example.com'},'organizer':{'self':True},
            'start':{'dateTime':start.isoformat()},'end':{'dateTime':(start+timedelta(minutes=minutes)).isoformat()},
            'extendedProperties':{'private':{**props,'original':'preserved'},'shared':{'public':'preserved'}}}
        return events[i]
    return planner,calendar,events,add


def preview(group,text='Reschedule ISPM chapters 1–3 in order.',update=1):
    planner=group[0]
    result=asyncio.run(module.preview(planner,123,text,update,NOW))
    if isinstance(result,dict):
        calendar_replan.bind(planner.db,123,50,result['messages'][0])
    return result,planner.db.get_task_action_state(calendar_replan.key(123))


def allow(group,state):
    return asyncio.run(calendar_replan.callback(group[0],123,50,state['token'],'allow',NOW))


def test_order_preserves_event_identity_duration_and_content(group):
    planner,calendar,events,add=group
    add(1,day=24,hour=9,minutes=90)
    add(2,hour=9,minutes=60)
    add(3,hour=13,minutes=120)
    before=deepcopy(events)
    result,state=preview(group)
    assert isinstance(result,dict),result
    assert [m['ordinal'] for m in state['calendar_moves']]==[1,2,3]
    windows=[tuple(datetime.fromisoformat(m[k]) for k in ('start','end')) for m in state['calendar_moves']]
    assert all(a>=NOW and (b-a).total_seconds()/60==state['calendar_moves'][i]['minutes'] for i,(a,b) in enumerate(windows))
    assert all(a[1]<=b[0] for a,b in zip(windows,windows[1:]))
    assert events==before
    calendar.move_event.assert_not_awaited()
    reply=allow(group,state)
    assert reply.startswith('Ordered rescheduling completed.'),reply
    assert set(events)==set(before)
    for i,e in events.items():
        for field in ('summary','description','location','attachments'):
            assert e[field]==before[i][field]
        assert e['extendedProperties']['private']['original']=='preserved'
        assert e['extendedProperties']['shared']==before[i]['extendedProperties']['shared']
    calendar.insert.assert_not_awaited()
    assert 'already been applied' in allow(group,state)
    assert calendar.move_event.await_count==3


def test_conflicts_cross_day_and_daily_budget_keep_order(group):
    planner,calendar,events,add=group
    planner.settings.learning_daily_minutes=240
    for i in range(1,6):add(i,minutes=120)
    planner.ai.parse_sequence_replan.return_value.ordinals=[1,2,3,4,5]
    other=deepcopy(add(8,subject='Other',hour=9,minutes=180))
    result,state=preview(group,'Reschedule ISPM chapters 1–5 in order.')
    assert isinstance(result,dict),result
    slots=[tuple(datetime.fromisoformat(m[k]) for k in ('start','end')) for m in state['calendar_moves']]
    assert len({a.date() for a,b in slots})>=3
    assert all(a[1]<=b[0] for a,b in zip(slots,slots[1:]))
    assert all(not(a<module.bounds(other,planner.settings.tz)[1] and b>module.bounds(other,planner.settings.tz)[0]) for a,b in slots)
    assert allow(group,state).startswith('Ordered rescheduling completed.')
    assert events['Other-8']==other


def test_confirmed_completed_kept_unknown_elapsed_replanned(group):
    planner,calendar,events,add=group
    first=add(1,day=24,linked=True)
    second=add(2,day=24,hour=10,linked=True)
    add(3,hour=9)
    tid=int(first['extendedProperties']['private']['chronosTask'])
    planner.db.complete_task(tid,NOW)
    planner.update(tid,lambda old:{**old,'status':'finished','feedback':{'outcome':'done'}})
    second_tid=int(second['extendedProperties']['private']['chronosTask'])
    planner.update(second_tid,lambda old:{**old,'status':'awaiting_feedback','delivery':'feedback_pending'})
    original=deepcopy(first)
    result,state=preview(group)
    assert isinstance(result,dict),result
    assert [m['ordinal'] for m in state['completed_events']]==[1]
    assert [m['ordinal'] for m in state['calendar_moves']]==[2,3]
    assert 'unknown' in state['calendar_moves'][0]['reason']
    reply=allow(group,state)
    assert reply.startswith('Ordered rescheduling completed.'),reply
    assert events['ISPM-1']==original
    assert planner.db.get_task(second_tid)['status']=='open'
    assert planner.get(second_tid)['status']=='scheduled'
    assert 'delivery' not in planner.get(second_tid)


@pytest.mark.parametrize('case',['missing','ambiguous','shared','deadline'])
def test_group_problems_are_collected_without_any_writes(group,case):
    planner,calendar,events,add=group
    for i in range(1,4):add(i,linked=case=='deadline')
    if case=='missing':
        del events['ISPM-1'];del events['ISPM-3']
    elif case=='ambiguous':
        events['duplicate']={**deepcopy(events['ISPM-2']),'id':'duplicate'}
    elif case=='shared':events['ISPM-1']['attendees']=[{'email':'someone@example.com'}]
    else:
        for task in planner.db.list_open_tasks():
            planner.db.edit_task(task['id'],task['title'],NOW.replace(day=25,hour=10),task['project'],{})
    before=deepcopy(events)
    result,state=preview(group)
    assert isinstance(result,str),result
    assert state is None or (case=='ambiguous' and state['status']=='clarifying' and 'calendar_moves' not in state)
    if case=='missing':assert 'Chapter 1' in result and 'Chapter 3' in result
    if case=='ambiguous':assert 'multiple candidates' in result
    if case=='deadline':assert 'deadline' in result
    assert events==before
    calendar.move_event.assert_not_awaited()
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('after_write',[False,True])
def test_partial_failure_and_uncertain_patch_recover_without_duplicates(group,after_write):
    planner,calendar,events,add=group
    for i in range(1,4):add(i,hour=9)
    result,state=preview(group)
    assert isinstance(result,dict),result
    original=calendar.move_event.side_effect
    failed=False
    async def fail(i,body,etag):
        nonlocal failed
        if (i=='ISPM-2' if not after_write else i=='ISPM-1') and not failed:
            failed=True
            if after_write:await original(i,body,etag)
            raise CalendarError('Response lost' if after_write else 'Temporary Calendar failure')
        return await original(i,body,etag)
    calendar.move_event.side_effect=fail
    reply=allow(group,state)
    assert 'whole group is not complete' in reply
    assert 'Updated and verified' in reply and 'Not updated' in reply
    paused=planner.db.get_task_action_state(calendar_replan.key(123))
    assert paused['status']=='paused'
    assert allow(group,state).startswith('Ordered rescheduling completed.')
    calls=[c.args[0] for c in calendar.move_event.await_args_list]
    assert calls.count('ISPM-1')==1
    assert len(calls)==3 if after_write else len(calls)==4
    calendar.insert.assert_not_awaited()


def test_generic_subject_range_preferences_and_original_slot(group):
    planner,calendar,events,add=group
    for i in (2,3,4):add(i,subject='Python',hour=13)
    planner.ai.parse_sequence_replan.return_value=SequenceReplanOutput(actionable=True,scope='Python',ordinals=[2,3,4],start_hour=13,end_hour=17,weekdays=[6,0,1,2,3,4,5])
    planner.db.mutate_task_action_state('owner:preferences:123',lambda old:{'study':'Afternoons, 13:00 to 17:00'})
    result,state=preview(group,'Reschedule Python chapters 2–4 in order.')
    assert isinstance(result,dict),result
    assert all(13<=datetime.fromisoformat(m['start']).hour<17 for m in state['calendar_moves'])
    assert state['calendar_moves'][0]['start']==state['calendar_moves'][0]['old_start']
    assert allow(group,state).startswith('Ordered rescheduling completed.')


@pytest.mark.parametrize('action',['cancel','edit','stale','lease'])
def test_review_gates_no_unapproved_or_stale_writes(group,action):
    planner,calendar,events,add=group
    for i in range(1,4):add(i,hour=9)
    result,state=preview(group)
    assert isinstance(result,dict),result
    if action in {'cancel','edit'}:
        reply=asyncio.run(calendar_replan.callback(planner,123,50,state['token'],action,NOW))
        assert 'cancelled' in reply or 'revised group' in reply
    elif action=='stale':
        events['ISPM-2']['etag']='external'
        assert 'whole group is not complete' in allow(group,state)
    else:
        planner.db.mutate_task_action_state(calendar_replan.key(123),lambda old:{**old,'status':'creating','lease':'other','lease_until':(NOW+timedelta(minutes=2)).isoformat()})
        assert 'no second writer' in allow(group,state)
    calendar.move_event.assert_not_awaited()


def test_primary_search_pagination_and_patch_contract(monkeypatch):
    cfg=Settings(_env_file=None,calendar_account='owner@example.com')
    seen=[]
    def handler(request):
        seen.append(request)
        if request.method=='PATCH':
            assert request.headers['if-match']=='v1'
            assert json.loads(request.content)=={'start':{'dateTime':'new'}}
            return httpx.Response(200,json={'id':'one'})
        page=request.url.params.get('pageToken')
        assert request.url.params['q']=='Python'
        return httpx.Response(200,json={'items':[{'id':page or 'one','summary':'Python Chapter 1'}],**({'nextPageToken':'two'} if not page else {})})
    cal=GoogleCalendar(cfg,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cal,'token',AsyncMock(return_value='test-token'))
    result=asyncio.run(cal.search_events('Python'))
    assert len(result)==2 and all(e['_calendar_id']==cfg.calendar_id for e in result)
    asyncio.run(cal.move_event('one',{'start':{'dateTime':'new'}},'v1'))
    assert len(seen)==3

def test_natural_request_routes_without_open_tasks_or_date(group,monkeypatch):
    from chronos import intents
    planner,calendar,events,add=group
    for i in range(1,4):add(i)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW.astimezone(tz)
    for mod in (main,intents,module):monkeypatch.setattr(mod,'datetime',Clock)
    for name,value in {'settings':planner.settings,'db':planner.db,'tasks':TaskService(planner.db,planner.settings.tz),
        'ai':planner.ai,'google_calendar':calendar,'learning_calendar':lambda:planner}.items():monkeypatch.setattr(main,name,value)
    action=asyncio.run(main.prepare_message('Reschedule ISPM chapters 1–3 in order.',update_id=500,chat_id=123))
    result=planner.db.process_update(500,action)
    assert planner.db.get_task_action_state(calendar_replan.key(123))['ordered_sequence']
    assert result['messages'][0]['reply_markup']['inline_keyboard'][0][0]['callback_data'].startswith('replan:allow:')
    planner.ai.classify_intent.assert_not_awaited()
    calendar.insert.assert_not_awaited()
    calendar.move_event.assert_not_awaited()


def test_preference_change_invalidates_review(group):
    planner,calendar,events,add=group
    for i in range(1,4):add(i)
    _,state=preview(group)
    planner.db.mutate_task_action_state('owner:preferences:123',lambda old:{'study':'Only afternoons'})
    assert 'preferences changed' in allow(group,state)
    calendar.move_event.assert_not_awaited()


def test_unknown_write_result_is_reported_and_verified_before_recovery(group):
    planner,calendar,events,add=group
    for i in range(1,4):add(i)
    _,state=preview(group)
    original=calendar.event.side_effect
    calls={}
    fail_reads=True
    def read(i):
        calls[i]=calls.get(i,0)+1
        if i=='ISPM-1' and calls[i]>1 and fail_reads:raise CalendarError('Read unavailable')
        return original(i)
    calendar.event.side_effect=read
    reply=allow(group,state)
    assert 'Result unverified' in reply and 'whole group is not complete' in reply
    assert calendar.move_event.await_count==1
    fail_reads=False
    assert allow(group,state).startswith('Ordered rescheduling completed.')
    assert [c.args[0] for c in calendar.move_event.await_args_list].count('ISPM-1')==1


def test_parser_schema_and_no_date_default(monkeypatch):
    cfg=Settings(_env_file=None,gemini_api_key='test-key',gemini_model='test-model')
    real=httpx.AsyncClient
    def respond(request):
        body=json.loads(request.content)
        assert 'ordinals' in body['generationConfig']['responseJsonSchema']['properties']
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps({
            'actionable':True,'scope':'Python','unit':'lecture','ordinals':[2,3,4]})}]}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(respond),**kw))
    parsed=asyncio.run(ExternalAI(cfg).parse_sequence_replan('Reschedule Python lectures 2–4 in order.',NOW,{}))
    assert parsed.ordinals==[2,3,4] and parsed.not_before is None and parsed.latest_end is None


def test_search_limit_fails_explicitly(monkeypatch):
    cal=GoogleCalendar(Settings(_env_file=None))
    monkeypatch.setattr(cal,'request',AsyncMock(return_value={'items':[],'nextPageToken':'more'}))
    with pytest.raises(CalendarError,match='narrow'):
        asyncio.run(cal.search_events('scope'))
    assert cal.request.await_count==4


@pytest.mark.parametrize('unit,text,title',[
    ('lecture','Reschedule Python lectures 2 to 4 in order.','Python Lecture 3'),
    ('unit','Reschedule Python units 2–4 in order.','Python Unit 3'),
    ('chapter','將資安第2到4章依序重排','讀資安第3章'),
])
def test_literal_ranges_and_generic_unit_matching(unit,text,title):
    assert module.literal_ordinals(text,unit)==[2,3,4]
    assert module.ordinal(title,'Python' if title.startswith('Python') else '資安',unit)==3


def test_ambiguous_date_answer_keeps_group_and_constraints(group):
    planner,calendar,events,add=group
    for i in range(1,4):add(i,hour=13)
    alternate=deepcopy(events['ISPM-1']);alternate['id']='alternate'
    alternate['start']['dateTime']='2026-10-29T13:00:00+08:00'
    alternate['end']['dateTime']='2026-10-29T14:00:00+08:00'
    events['alternate']=alternate
    planner.ai.parse_sequence_replan.return_value.start_hour=13
    result,state=preview(group)
    assert isinstance(result,str) and state['status']=='clarifying'
    assert '2026-10-29' in result and 'ref:alternate' in result
    planner.ai.parse_sequence_replan.return_value=SequenceReplanOutput(actionable=True,scope='ISPM',ordinals=[1,2,3],choices={'1':'alternate'})
    result,state=preview(group,'10/29 那個',2)
    assert isinstance(result,dict),result
    assert state['parsed']['start_hour']==13
    assert [m['ordinal'] for m in state['calendar_moves']]==[1,2,3]
    assert state['calendar_moves'][0]['event_id']=='alternate'
    assert all(datetime.fromisoformat(m['start']).hour>=13 for m in state['calendar_moves'])
    assert allow(group,state).startswith('Ordered rescheduling completed.')
    assert events['ISPM-1']['etag']=='v1'


def test_model_cannot_select_ambiguous_event_without_owner_evidence(group):
    planner,calendar,events,add=group
    for i in range(1,4):add(i)
    events['alternate']={**deepcopy(events['ISPM-1']),'id':'alternate'}
    _,pending=preview(group)
    assert pending['status']=='clarifying'
    planner.ai.parse_sequence_replan.return_value.choices={'1':'alternate'}
    result,state=preview(group,'I am not sure',2)
    assert isinstance(result,str) and state['status']=='clarifying'
    calendar.move_event.assert_not_awaited()

def test_final_lost_response_is_reported_completed_only_after_readback(group):
    planner,calendar,events,add=group
    for i in range(1,4):add(i)
    _,state=preview(group)
    original=calendar.move_event.side_effect
    async def lost(i,body,etag):
        result=await original(i,body,etag)
        if i=='ISPM-3':raise CalendarError('Last response lost')
        return result
    calendar.move_event.side_effect=lost
    reply=allow(group,state)
    assert reply.startswith('Ordered rescheduling completed.'),reply
    assert planner.db.get_task_action_state(calendar_replan.key(123))['status']=='done'


def test_partial_failure_can_be_replanned_explicitly_with_edit(group):
    planner,calendar,events,add=group
    for i in range(1,4):add(i)
    _,state=preview(group)
    original=calendar.move_event.side_effect
    async def fail(i,body,etag):
        if i=='ISPM-2':raise CalendarError('Temporary failure')
        return await original(i,body,etag)
    calendar.move_event.side_effect=fail
    assert 'whole group is not complete' in allow(group,state)
    reply=asyncio.run(calendar_replan.callback(planner,123,50,state['token'],'edit',NOW))
    assert 'revised group' in reply
    assert planner.db.get_task_action_state('sequence:batch:'+state['token'])['applied_event_ids']==['ISPM-1']
    calendar.move_event.side_effect=original
    result,new_state=preview(group,'Reschedule ISPM chapters 1–3 in order.',2)
    assert isinstance(result,dict),result
    assert new_state['token']!=state['token']
    assert allow(group,new_state).startswith('Ordered rescheduling completed.')

def test_large_review_splits_before_confirmation_without_losing_items():
    state={'token':'a'*32,'completed_events':[], 'calendar_moves':[
        {'title':('<&>'*30)+str(n),'old_start':'2026-10-25T09:00:00+08:00','old_end':'2026-10-25T10:00:00+08:00',
         'start':'2026-10-26T09:00:00+08:00','end':'2026-10-26T10:00:00+08:00','minutes':60,'reason':'Order and constraints checked.'}
        for n in range(20)]}
    messages=module.card(state)['messages']
    assert len(messages)>1
    assert all(len(m['text'])<=3500 for m in messages)
    assert all('replan_token' not in m for m in messages[:-1])
    assert messages[-1]['replan_token']=='a'*32
    assert sum(m['text'].count('Order and constraints checked.') for m in messages)==20

def test_late_original_preference_does_not_cause_false_deadline_failure(group):
    planner,calendar,events,add=group
    add(1,hour=19,linked=True)
    second=add(2,hour=13,minutes=120,linked=True)
    add(3,hour=15,linked=True)
    tid=int(second['extendedProperties']['private']['chronosTask'])
    task=planner.db.get_task(tid)
    planner.db.edit_task(tid,task['title'],NOW.replace(day=25,hour=17,minute=0),task['project'],{})
    result,state=preview(group)
    assert isinstance(result,dict),result
    assert datetime.fromisoformat(state['calendar_moves'][0]['start']).hour < 19
    assert datetime.fromisoformat(state['calendar_moves'][1]['end']) <= NOW.replace(day=25,hour=17,minute=0)
    assert allow(group,state).startswith('Ordered rescheduling completed.')

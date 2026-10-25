import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import httpx

from chronos import event_reset as reset, study_reset as core, main
from chronos.google_calendar import CalendarError
from chronos.tasks import TaskService
from test_learning_calendar import setup, NOW
from test_intents import store, run, classify


def initialize(setup, event_type='meeting', *, past=False):
    planner, task, calendar, _ = setup
    events={}
    for n,category in enumerate((event_type,event_type,'social')):
        a=NOW+timedelta(days=-1 if past and n==0 else n+1)
        event={'id':f'old{n}','etag':'v1','summary':f'Target {event_type} {n}' if n<2 else 'Other social event',
            '_calendar_id':'primary','creator':{'email':planner.settings.calendar_account},'organizer':{'self':True},
            'description':'Keep these details','location':'Office','colorId':'5','visibility':'private','transparency':'opaque',
            'reminders':{'useDefault':False,'overrides':[{'method':'popup','minutes':15}]},
            'start':{'dateTime':a.isoformat()},'end':{'dateTime':(a+timedelta(hours=1)).isoformat()},
            'extendedProperties':{'private':{'chronosEventCategory':category,'custom':'keep'},'shared':{'ownerNote':'keep'}}}
        events[event['id']]=event
    controls={}
    planner.ai=AsyncMock()
    async def parse(kind,instruction,items,context,now):
        choices=[reset.ItemChoice(item_id=i['id'],selected=i['title'].startswith('Target'),minutes=30) for i in items]
        if controls.get('omit'):
            choices.pop()
        selected=[i.item_id for i in choices if i.selected]
        dependencies=[core.Dependency(before=selected[0],after=selected[1])] if len(selected)>1 else []
        if controls.get('cycle'):
            dependencies.append(core.Dependency(before=selected[1],after=selected[0]))
        return reset.ResetOutput(actionable=not controls.get('ambiguous',False),include_past=controls.get('include_past',True),
            items=choices,dependencies=dependencies,start=controls.get('start'),end=controls.get('end'),
            question='Which events?' if controls.get('ambiguous') else None)
    planner.ai.parse_event_reset.side_effect=parse
    calendar.agenda.side_effect=lambda a,b:[deepcopy(e) for e in events.values() if core.bounds(e,planner.settings.tz)[0]<b and core.bounds(e,planner.settings.tz)[1]>a]
    calendar.category_events.side_effect=lambda t,**kw:[deepcopy(e) for e in events.values()
        if e.get('extendedProperties',{}).get('private',{}).get('chronosEventCategory')==t
        and (not kw.get('start') or core.bounds(e,planner.settings.tz)[1]>kw['start'])
        and (not kw.get('end') or core.bounds(e,planner.settings.tz)[0]<kw['end'])]
    calendar.task_events.side_effect=lambda i:[deepcopy(e) for e in events.values() if e.get('extendedProperties',{}).get('private',{}).get('chronosTask')==str(i)]
    calendar.event.side_effect=lambda i:deepcopy(events.get(i))
    async def insert(body):
        assert body['id'] not in events
        events[body['id']]={**deepcopy(body),'etag':'new','_calendar_id':'primary',
            'creator':{'email':planner.settings.calendar_account},'organizer':{'self':True}}
        return deepcopy(events[body['id']])
    async def delete(i,etag):
        assert events[i]['etag']==etag
        del events[i]
    calendar.insert.side_effect=insert
    calendar.delete_event.side_effect=delete
    calendar.busy.side_effect=lambda a,b:[core.bounds(e,planner.settings.tz) for e in events.values()
        if core.bounds(e,planner.settings.tz)[0]<b and core.bounds(e,planner.settings.tz)[1]>a]
    return planner,events,controls


def rebuild(planner,category='meeting',text='',update=1):
    return asyncio.run(reset.run(planner,123,category,text,update,NOW))


@pytest.mark.parametrize('category',['meeting','workout','custom-type'])
def test_variable_category_rebuilds_only_selected_standalone_events_and_preserves_details(setup,category):
    planner,events,controls=initialize(setup,category)
    other=deepcopy(events['old2'])
    assert '已全部重建' in rebuild(planner,category)
    assert 'old0' not in events and 'old1' not in events and events['old2']==other
    state=planner.db.get_task_action_state(core.KEY)
    assert state['event_type']==category and state['status']=='completed'
    sessions=state['sessions']
    assert len(sessions)==2 and sessions[0]['end']<=sessions[1]['start']
    assert all(s['minutes']==60 for s in sessions)  # Model's 30-minute estimate cannot shorten an event.
    for session in sessions:
        event=events[session['event_id']]
        assert event['description']=='Keep these details' and event['location']=='Office'
        assert event['reminders']['overrides'][0]['minutes']==15
        assert event['extendedProperties']['private']['chronosEventCategory']==category
        assert event['extendedProperties']['private']['custom']=='keep'
        assert event['extendedProperties']['shared']=={'ownerNote':'keep'}
        assert 'chronosTask' not in event['extendedProperties']['private']
    assert len(planner.db.list_open_tasks())==1  # No fake tasks created for standalone events.
    before=deepcopy(events)
    assert '已全部重建' in rebuild(planner,category)
    assert events==before and planner.calendar.insert.await_count==2


@pytest.mark.parametrize('failure',['cycle','omit','ambiguous','shared','attachment','wrong_type','no_space'])
def test_invalid_or_unsafe_category_plan_never_deletes_originals(setup,failure):
    planner,events,controls=initialize(setup)
    if failure in {'cycle','omit','ambiguous'}:
        controls[failure]=True
    elif failure=='shared':
        events['old0']['attendees']=[{'email':'other@example.com'}]
    elif failure=='attachment':
        events['old0']['attachments']=[{'title':'Keep attachment'}]
    elif failure=='wrong_type':
        events['old0']['extendedProperties']['private']['chronosEventCategory']='exam'
    else:
        controls['end']=(NOW+timedelta(minutes=11)).isoformat()
    before=deepcopy(events)
    assert '尚未' in rebuild(planner)
    assert events==before
    planner.calendar.insert.assert_not_awaited()
    planner.calendar.delete_event.assert_not_awaited()


def test_historical_question_keeps_category_and_short_answer_rebuilds_that_type(setup):
    planner,events,controls=initialize(setup,'workout',past=True)
    controls['include_past']=None
    assert '是否包含' in rebuild(planner,'workout')
    controls['include_past']=True
    assert '已全部重建' in asyncio.run(reset.resume(planner,123,'包含',2,NOW))
    assert planner.db.get_task_action_state(core.KEY)['event_type']=='workout'
    context=planner.ai.parse_event_reset.call_args.args[3]
    assert context['clarification']['event_type']=='workout'


@pytest.mark.parametrize('lost',['insert','delete'])
def test_category_unknown_outcome_recovers_without_duplicate_events(setup,lost):
    planner,events,controls=initialize(setup)
    method=planner.calendar.insert if lost=='insert' else planner.calendar.delete_event
    original=method.side_effect
    async def interrupted(*args):
        await original(*args)
        raise CalendarError('response lost')
    method.side_effect=interrupted
    assert '重建暫停' in rebuild(planner)
    assert asyncio.run(planner._tick(NOW))=={'waiting_for':'study rebuild reconciliation'}
    method.side_effect=original
    assert '已全部重建' in rebuild(planner,update=2)
    assert len(events)==3 and planner.calendar.insert.await_count==2


def test_standalone_booking_ledger_follows_new_event_id(setup):
    planner,events,controls=initialize(setup)
    source=events['old0']
    source['extendedProperties']['private']['chronosFreeToken']='booking1'
    planner.db.mutate_task_action_state('free:booking:booking1',lambda old:{'status':'booked','token':'booking1',
        'event_id':'old0','start':source['start']['dateTime'],'end':source['end']['dateTime']})
    assert '已全部重建' in rebuild(planner)
    saved=planner.db.get_task_action_state('free:booking:booking1')
    assert saved['event_id']!='old0' and saved['event_id'] in events
    assert events[saved['event_id']]['extendedProperties']['private']['chronosFreeToken']=='booking1'


def test_generic_rebuild_includes_unbooked_tasks_without_touching_study(setup):
    planner,events,controls=initialize(setup,'work')
    task=planner.db.create_task('Target write client proposal',None,'work',NOW,{'estimated_minutes':90})
    assert '已全部重建' in rebuild(planner,'work')
    saved=planner.get(task['id'])
    assert saved['status']=='scheduled' and saved['minutes']==90
    assert events[saved['event_id']]['extendedProperties']['private']['chronosEventCategory']=='work'
    study=planner.db.list_open_tasks()[0]
    assert study['title']=='Study graphs' and planner.get(study['id']) is None


@pytest.mark.parametrize('text',['/eventreset','/eventreset help','/eventreset all'])
def test_missing_help_and_wildcard_types_never_execute(setup,text):
    planner,events,controls=initialize(setup)
    assert asyncio.run(reset.command(planner,123,text,1,NOW))
    planner.ai.parse_event_reset.assert_not_awaited()
    planner.calendar.delete_event.assert_not_awaited()


@pytest.mark.parametrize('text',['/eventreset meeting','/eventreset@ChronosBot workout 只排未來'])
def test_generic_command_is_direct_and_category_is_data(store,monkeypatch,text):
    monkeypatch.setattr(main.settings,'enable_learning_calendar',True)
    execute=AsyncMock(return_value='rebuilt')
    monkeypatch.setattr(reset,'run',execute)
    classifier=classify(monkeypatch,action='unsupported')
    assert run(text)['reply']=='rebuilt'
    execute.assert_awaited_once()
    assert execute.call_args.args[2] in {'meeting','workout'}
    classifier.assert_not_awaited()


def test_study_type_preserves_existing_service_and_alias(setup,monkeypatch):
    planner,events,controls=initialize(setup)
    old=AsyncMock(return_value='study result')
    monkeypatch.setattr(core,'run',old)
    assert asyncio.run(reset.command(planner,123,'/eventreset study',1,NOW))=='study result'
    assert asyncio.run(core.command(planner,123,'/studyreset',2,NOW))=='study result'
    assert old.await_count==2


def test_primary_email_alias_does_not_duplicate_sources(setup):
    planner,events,controls=initialize(setup)
    planner.settings.calendar_id=planner.settings.calendar_account
    original=planner.calendar.category_events.side_effect
    planner.calendar.category_events.side_effect=lambda t,**kw:[{**e,'_calendar_id':planner.settings.calendar_account} for e in original(t,**kw)]
    assert '已全部重建' in rebuild(planner)
    assert len(events)==3 and planner.calendar.insert.await_count==2


def test_future_only_preserves_historical_event_and_bounds_discovery(setup):
    planner,events,controls=initialize(setup,past=True)
    before=deepcopy(events['old0'])
    assert '已全部重建' in rebuild(planner,text='只排未來')
    assert events['old0']==before
    assert planner.calendar.category_events.call_args.kwargs=={'start':NOW,'end':NOW+timedelta(days=30)}


def test_booked_task_preserves_segment_details_and_adds_explicit_remaining_effort(setup):
    from chronos.task_actions import task_fields
    planner,events,controls=initialize(setup,'work')
    task=planner.db.create_task('Target client report',None,'work',NOW,{'estimated_minutes':180})
    source=deepcopy(events['old0'])
    source.update(id='task-event',summary=task['title'])
    source['extendedProperties']['private'].update(chronosTask=str(task['id']),chronosToken='old-task-token')
    events['task-event']=source
    planner.update(task['id'],lambda old:{'status':'scheduled','event_id':'task-event','token':'old-task-token',
        'start':source['start']['dateTime'],'end':source['end']['dateTime'],'minutes':60,'snapshot':task_fields(task)})
    assert '已全部重建' in rebuild(planner,'work')
    saved=planner.get(task['id'])
    assert saved['minutes']==60
    assert sum(s['minutes'] for s in [saved,*saved['reset_remaining_sessions']])==180
    event=events[saved['event_id']]
    assert event['description']==source['description'] and event['reminders']==source['reminders']
    assert event['extendedProperties']['private']['custom']=='keep'
    assert event['extendedProperties']['private']['chronosToken']!='old-task-token'


@pytest.mark.parametrize('direction',['before','after'])
def test_unselected_event_can_anchor_dependency_without_moving_it(setup,direction):
    planner,events,controls=initialize(setup)
    original=planner.ai.parse_event_reset.side_effect
    async def anchored(kind,instruction,items,context,now):
        output=await original(kind,instruction,items,context,now)
        selected=next(i['id'] for i in items if i['title'].startswith('Target'))
        fixed=next(i['id'] for i in items if i['title']=='Other social event')
        output.dependencies.append(core.Dependency(before=selected if direction=='before' else fixed,
            after=fixed if direction=='before' else selected))
        return output
    planner.ai.parse_event_reset.side_effect=anchored
    fixed=deepcopy(events['old2'])
    assert '已全部重建' in rebuild(planner)
    state=planner.db.get_task_action_state(core.KEY)
    session=state['sessions'][0]
    if direction=='before':
        assert session['end']<=fixed['start']['dateTime']
    else:
        assert session['start']>=fixed['end']['dateTime']
    assert events['old2']==fixed


@pytest.mark.parametrize('historical',[False,True])
def test_real_webhook_category_reset_replay_and_short_scope_answer(setup,monkeypatch,historical):
    from chronos import calendar_replan
    planner,events,controls=initialize(setup,'workout',past=historical)
    if historical:
        controls['include_past']=None
    class Clock(reset.datetime):
        @classmethod
        def now(cls,tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(reset,'datetime',Clock)
    monkeypatch.setattr(main,'settings',planner.settings)
    monkeypatch.setattr(main,'db',planner.db)
    monkeypatch.setattr(main,'tasks',TaskService(planner.db,planner.settings.tz))
    monkeypatch.setattr(main,'learning_calendar',lambda:planner)
    monkeypatch.setattr(main,'telegram',planner.telegram)
    planner.db.mutate_task_action_state(calendar_replan.key(123),lambda old:{'status':'editing','ordered_sequence':True,'expires_at':'2099-01-01T00:00:00+08:00'})
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            message={'message_id':1,'chat':{'id':123},'text':'/eventreset@ChronosBot workout'}
            assert (await client.post('/telegram/webhook',json={'update_id':1,'message':message})).status_code==200
            if historical:
                assert 'old0' in events and planner.calendar.insert.await_count==0
                controls['include_past']=True
                assert (await client.post('/telegram/webhook',json={'update_id':2,'message':{'message_id':2,'chat':{'id':123},'text':'包含'}})).status_code==200
            assert (await client.post('/telegram/webhook',json={'update_id':1,'message':message})).status_code==200
            assert (await client.post('/telegram/webhook',json={'update_id':3,'edited_message':message})).status_code==200
    asyncio.run(send())
    assert 'old0' not in events and 'old1' not in events and 'old2' in events
    assert planner.calendar.insert.await_count==2
    assert planner.ai.parse_event_reset.await_count==2 if historical else planner.ai.parse_event_reset.await_count==1


def test_natural_type_request_uses_generic_intent(store,monkeypatch):
    monkeypatch.setattr(main.settings,'enable_learning_calendar',True)
    execute=AsyncMock(return_value='generic rebuilt')
    monkeypatch.setattr(reset,'run',execute)
    classify(monkeypatch,action='event_reset',reset_type='workout')
    assert run('把所有健身活動重新安排')['reply']=='generic rebuilt'
    assert execute.call_args.args[2]=='workout'


def test_multi_page_rebuild_receipt_retries_only_undelivered_page(setup,monkeypatch):
    from fastapi import HTTPException
    planner,*_=setup
    monkeypatch.setattr(main,'db',planner.db)
    monkeypatch.setattr(main,'telegram',planner.telegram)
    receipt=planner.db.process_update(77,lambda:{'messages':[{'text':t,'event_reset_result':True,'parse_mode':'HTML'} for t in ('first','second')]})
    planner.telegram.send_message.side_effect=[{'ok':True},{'ok':False},{'ok':True}]
    with pytest.raises(HTTPException):
        asyncio.run(main.deliver_learning_reply(123,77,receipt))
    saved=planner.db.get_update(77)
    assert saved['delivered_count']==1 and not saved['delivered']
    asyncio.run(main.deliver_learning_reply(123,77,saved))
    assert [c.args[1] for c in planner.telegram.send_message.call_args_list]==['first','second','second']
    assert planner.db.get_update(77)['delivered']


def test_shared_operation_cannot_resume_under_another_type(setup):
    planner,events,controls=initialize(setup)
    original=planner.calendar.insert.side_effect
    async def lost(body):
        await original(body)
        raise CalendarError('response lost')
    planner.calendar.insert.side_effect=lost
    rebuild(planner)
    before=deepcopy(events)
    assert '另一類型' in rebuild(planner,'social',update=2)
    assert '另一類型' in asyncio.run(core.run(planner,123,'all study reset',3,NOW))
    assert events==before


def test_long_event_titles_cannot_overflow_receipt_pages():
    title='&'*1000
    state={'status':'completed','event_type':'workout','factor':1,'sessions':[
        {'title':title,'start':NOW.isoformat(),'end':(NOW+timedelta(hours=1)).isoformat(),'minutes':60,'original_minutes':60}
        for _ in range(10)]}
    reply=core.result(state)
    assert isinstance(reply,dict)
    assert all(0<len(m['text'])<=3500 and m['event_reset_result'] for m in reply['messages'])
    assert all(s['title']==title for s in state['sessions'])

import asyncio
from datetime import datetime,timedelta
from unittest.mock import AsyncMock
import pytest

from chronos import main,study_revision,calendar_replan,learning_reply
from chronos.ai import StudyRevisionOutput,StudyRevisionItem
from chronos.intents import Intent
from chronos.learning_calendar import LearningCalendar
from chronos.task_actions import task_fields
from chronos.study_constraints import literal_window
from chronos.backward_planning import reserved_windows
from chronos.evening_agenda import render_agenda
from test_system import system
from test_intents import store
from test_learning_calendar import setup

MORNING=datetime.fromisoformat('2026-10-29T04:23:00+08:00')
TOKEN='a'*32


@pytest.mark.parametrize('direct',[False,True])
@pytest.mark.parametrize('kind',['morning','afternoon'])
def test_scheduling_requests_escape_stale_feedback_prompt(system,store,monkeypatch,direct,kind):
    client,_,bot,config=system
    config.enable_learning_calendar=True
    monkeypatch.setattr(LearningCalendar,'tick',AsyncMock(return_value={}))
    calendar=AsyncMock();calendar.configured=lambda:True
    monkeypatch.setattr(main,'google_calendar',calendar)
    task=main.tasks.create('Old reading')
    planner=main.learning_calendar()
    planner.update(task['id'],lambda _: {'status':'awaiting_feedback','token':TOKEN,'snapshot':task_fields(task)})
    main.db.edit_task_with_undo(task['id'],'Updated reading',None,None,{})
    ref={'task_id':task['id'],'token':TOKEN,'mode':'feedback','expires_at':(datetime.now(config.tz)+timedelta(hours=2)).isoformat()}
    main.db.mutate_task_action_state('learning:edit-chat:123',lambda _:ref)
    main.db.mutate_task_action_state('learning:edit-message:123:50',lambda _:ref)
    classifier=AsyncMock(return_value=Intent(action='study_replan' if kind=='morning' else 'replan',instruction='DB03,04,05 each 60 min before 09:00' if kind=='morning' else '今天下午留給競程準備，移除讀書計畫'))
    monkeypatch.setattr(main.ai,'classify_intent',classifier)
    preview=AsyncMock(return_value={'messages':[{'text':'新的排程提案'}]})
    monkeypatch.setattr(study_revision if kind=='morning' else calendar_replan,'preview',preview)
    message={'message_id':8101,'chat':{'id':123},'text':'將 DB03,04,05 分別安排到現在到今天早上九點，每個行程間有短休息' if kind=='morning' else '今天下午的時間留給競程期中考準備，移除該時段現有的讀書計畫'}
    if direct:message['reply_to_message']={'message_id':50,'chat':{'id':123},'text':'舊回報'}
    payload={'update_id':8101,'message':message}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    classifier.assert_awaited_once();preview.assert_awaited_once()
    assert not main.db.get_task_action_state('learning:edit-chat:123')
    assert planner.get(task['id'])['status']=='awaiting_feedback'
    assert main.db.get_task(task['id'])['status']=='open'
    assert '新的排程提案' in bot.send_message.await_args.args[1]


def test_genuine_stale_answer_exits_context_and_requires_explicit_refresh(system,store,monkeypatch):
    client,_,bot,config=system
    config.enable_learning_calendar=True
    monkeypatch.setattr(LearningCalendar,'tick',AsyncMock(return_value={}))
    task=main.tasks.create('Old reading')
    planner=main.learning_calendar()
    planner.update(task['id'],lambda _: {'status':'awaiting_feedback','token':TOKEN,'snapshot':task_fields(task)})
    main.db.edit_task_with_undo(task['id'],'Updated reading',None,None,{})
    main.db.mutate_task_action_state('learning:edit-chat:123',lambda _: {'task_id':task['id'],'token':TOKEN,'mode':'feedback',
        'expires_at':(datetime.now(config.tz)+timedelta(hours=2)).isoformat()})
    assert client.post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json={
        'update_id':8102,'message':{'message_id':8102,'chat':{'id':123},'text':'讀了兩小時，到第20頁'}}).status_code==200
    assert not main.db.get_task_action_state('learning:edit-chat:123')
    markup=bot.send_message.await_args.kwargs['reply_markup']
    action,task_id,ref_token=markup['inline_keyboard'][0][0]['callback_data'].split(':')[1:]
    result=asyncio.run(planner.callback(action,int(task_id),ref_token,50))
    assert result['messages'][0]['learning_edit']['mode']=='feedback'
    assert planner.get(task['id'])['snapshot']==task_fields(main.db.get_task(task['id']))
    planner.update(task['id'],lambda old:{**old,'partial_draft':{'minutes':120}})
    assert isinstance(asyncio.run(planner.callback(action,int(task_id),ref_token,50)),str)
    assert planner.get(task['id'])['partial_draft']=={'minutes':120}


def prepare(planner,calendar,breaks=5):
    tasks=[planner.db.create_task('Database systems Chapter '+str(n),None,'database',MORNING) for n in (3,4,5)]
    tasks.append(planner.db.create_task('Database systems midterm past papers',None,'database',MORNING))
    planner.ai=AsyncMock()
    planner.ai.parse_study_revision.return_value=StudyRevisionOutput(actionable=True,day='2026-10-29',items=[
        StudyRevisionItem(task_id=t['id'],minimum_minutes=60 if n<3 else 90) for n,t in enumerate(tasks)])
    calendar.agenda.return_value=[];calendar.busy.return_value=[]
    text=f'將 DB03,04,05 分別至少分配一個小時，以及90分鐘的DB考古準備，安排到現在到今天早上九點，每個行程間休息{breaks}分鐘'
    return tasks,text


def test_capacity_includes_all_tasks_and_breaks_without_writes(setup):
    planner,_,calendar,_=setup
    tasks,text=prepare(planner,calendar,5)
    result=asyncio.run(study_revision.preview(planner,123,text,8201,MORNING))
    assert isinstance(result,str) and all(s in result for s in ['270','285','276','9'])
    assert not planner.db.get_task_action_state(calendar_replan.key(123))
    calendar.insert.assert_not_awaited();calendar.move_event.assert_not_awaited()


def test_feasible_morning_window_preserves_durations_rests_and_booking_limits(setup):
    planner,_,calendar,_=setup
    tasks,text=prepare(planner,calendar,2)
    planner.settings.learning_daily_minutes=720
    older=planner.db.list_open_tasks()[0]
    planner.update(older['id'],lambda _: {'status':'proposed','token':'older',
        'start':MORNING.replace(hour=13).isoformat(),'end':MORNING.replace(hour=14).isoformat(),'minutes':60})
    planner.db.mutate_task_action_state('learning:proposal-queue',lambda _: {'active_task_id':older['id'],'requested_task_ids':[older['id']]})
    begin=MORNING.replace(minute=0)
    result=asyncio.run(study_revision.preview(planner,123,text,8202,begin))
    assert isinstance(result,dict),result
    review=planner.db.get_task_action_state(calendar_replan.key(123))
    assert [a['minutes'] for a in review['revision_additions']]==[60,60,60,90]
    assert len(review['revision_breaks'])==3
    assert all(a['end']<='2026-10-29T09:00:00+08:00' for a in review['revision_additions'])
    calendar_replan.bind(planner.db,123,50,result['messages'][0])
    reply=asyncio.run(calendar_replan.callback(planner,123,50,review['token'],'allow',begin))
    assert 'Queued' in reply['messages'][0]['text']
    assert len(reserved_windows(planner.db))==3
    assert planner.db.get_task_action_state('learning:proposal-queue')['active_task_id']==tasks[0]['id']
    planner.settings.learning_auto_accept=True
    for minute in range(4):asyncio.run(planner.tick(begin+timedelta(minutes=minute),force_proposal=True))
    assert calendar.insert.await_count==4,{t['title']:(planner.get(t['id']).get('status'),planner.get(t['id']).get('nightly_invalid_reasons'),planner.get(t['id']).get('minutes')) for t in tasks if planner.get(t['id']).get('status')!='scheduled'}
    for t in tasks:
        state=planner.get(t['id'])
        assert state['status']=='scheduled',state
        assert state['end']<='2026-10-29T09:00:00+08:00'
    assert all(not(t['start']<s['end'] and t['end']>s['start']) for t in [planner.get(x['id']) for x in tasks] for s in review['revision_breaks'])


def test_elapsed_window_never_rolls_to_another_day():
    with pytest.raises(ValueError):literal_window('現在到今天早上九點',MORNING.replace(hour=10))


def test_afternoon_reservation_does_not_move_exam_or_personal_events(setup):
    from copy import deepcopy
    planner,task,calendar,_=setup
    now=MORNING
    planner.db.edit_task(task['id'],task['title'],MORNING+timedelta(days=3),task['project'],{})
    task=planner.db.get_task(task['id'])
    start=now.replace(hour=14);end=now.replace(hour=15)
    planner.update(task['id'],lambda _: {'status':'scheduled','token':TOKEN,'event_id':'study','minutes':60,
        'start':start.isoformat(),'end':end.isoformat(),'snapshot':task_fields(task)})
    events={'study':{'id':'study','etag':'v1','summary':task['title'],'_calendar_id':'primary',
        'creator':{'email':'owner@example.com'},'organizer':{'self':True},'start':{'dateTime':start.isoformat()},'end':{'dateTime':end.isoformat()},
        'extendedProperties':{'private':{'chronosTask':str(task['id']),'chronosToken':TOKEN}}},
        'exam':{'id':'exam','summary':'competition programming midterm','_calendar_id':'primary',
            'start':{'dateTime':now.replace(hour=18,minute=30).isoformat()},'end':{'dateTime':now.replace(hour=21,minute=30).isoformat()}},
        'meeting':{'id':'meeting','summary':'Fixed meeting','_calendar_id':'primary','creator':{'email':'owner@example.com'},'organizer':{'self':True},
            'start':{'dateTime':now.replace(hour=13).isoformat()},'end':{'dateTime':now.replace(hour=14).isoformat()},'etag':'meeting-etag'}}
    calendar.agenda.side_effect=lambda *args:deepcopy(list(events.values()))
    calendar.event.side_effect=lambda i:deepcopy(events.get(i))
    async def move(i,body,etag):
        events[i].update(body);return deepcopy(events[i])
    calendar.move_event.side_effect=move
    result=asyncio.run(calendar_replan.preview(planner,123,'今天下午的時間留給 competition programming midterm 準備，移除該時段現有的讀書計畫',8301,now))
    assert isinstance(result,dict),result
    review=planner.db.get_task_action_state(calendar_replan.key(123))
    assert [m['event_id'] for m in review['calendar_moves']]==['study']
    assert review['focus_reservation']['start'][11:16]=='13:00'
    calendar_replan.bind(planner.db,123,50,result['messages'][0])
    reply=asyncio.run(calendar_replan.callback(planner,123,50,review['token'],'allow',now))
    assert '下午已保留' in reply['messages'][0]['text'],reply
    assert calendar.move_event.await_args.args[0]=='study'
    assert events['exam']['start']['dateTime']==now.replace(hour=18,minute=30).isoformat()
    shown=render_agenda(list(events.values()),now.date(),planner.settings.tz,label='Today',db=planner.db,now=now,show_project_tags=True)
    assert any('保留時段' in part for part in shown)


def test_daily_limit_is_checked_even_when_window_and_short_breaks_fit(setup):
    planner,_,calendar,_=setup
    tasks,text=prepare(planner,calendar,2)
    result=asyncio.run(study_revision.preview(planner,123,text,8401,MORNING))
    assert isinstance(result,str) and '每日上限' in result
    assert not planner.db.get_task_action_state(calendar_replan.key(123))
    calendar.insert.assert_not_awaited()


def test_reservations_are_visible_and_owner_can_release_without_undoing_moves(setup):
    from chronos.planning_holds import save,release
    planner,_,calendar,_=setup
    save(planner.db,'hold-token',[{'start':MORNING.replace(hour=13).isoformat(),'end':MORNING.replace(hour=18).isoformat(),
        'title':'競程準備','chat_id':123}],MORNING)
    assert len(reserved_windows(planner.db))==1
    release(planner.db,456,'hold-token')
    assert len(reserved_windows(planner.db))==1
    assert '已解除' in release(planner.db,123,'hold-token')
    assert not reserved_windows(planner.db)
    calendar.move_event.assert_not_awaited();calendar.delete_event.assert_not_awaited()


def test_class_preparation_remains_hard_even_with_large_daily_budget(setup):
    planner,_,calendar,_=setup
    tasks,text=prepare(planner,calendar,2)
    planner.settings.learning_daily_minutes=720
    result=asyncio.run(study_revision.preview(planner,123,text,8402,MORNING))
    assert isinstance(result,str) and '課前準備' in result
    assert not planner.db.get_task_action_state(calendar_replan.key(123))
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('change',['elapsed','busy'])
def test_review_rechecks_window_and_break_conflicts_before_any_writes(setup,change):
    planner,_,calendar,_=setup
    tasks,text=prepare(planner,calendar,2)
    planner.settings.learning_daily_minutes=720
    now=MORNING.replace(minute=0)
    result=asyncio.run(study_revision.preview(planner,123,text,8501,now))
    assert isinstance(result,dict)
    review=planner.db.get_task_action_state(calendar_replan.key(123))
    calendar_replan.bind(planner.db,123,50,result['messages'][0])
    if change=='busy':
        rest=review['revision_breaks'][0]
        calendar.agenda.return_value=[{'id':'new-fixed','_calendar_id':'primary','summary':'New commitment',
            'start':{'dateTime':rest['start']},'end':{'dateTime':rest['end']}}]
    result=asyncio.run(calendar_replan.callback(planner,123,50,review['token'],'allow',now+timedelta(minutes=3) if change=='elapsed' else now))
    assert isinstance(result,str) and 'Nothing changed' in result
    calendar.insert.assert_not_awaited();calendar.move_event.assert_not_awaited()
    assert not reserved_windows(planner.db)


def test_new_planning_request_does_not_inherit_an_old_approved_window(setup):
    planner,task,_,_=setup
    planner.update(task['id'],lambda _: {'status':'no_slot','token':TOKEN,'duration_source':'nightly_ai',
        'owner_window':{'start':MORNING.isoformat(),'end':MORNING.replace(hour=9).isoformat()},
        'nightly_slot':{'start':MORNING.isoformat(),'end':MORNING.replace(hour=5).isoformat()},'protected_breaks':[]})
    _,ok=planner.request_proposals([task['id']],minutes=60)
    assert ok
    assert 'owner_window' not in planner.get(task['id'])


def test_break_followup_keeps_original_scope_and_replaces_old_break_length():
    from chronos.study_constraints import revision_instruction,rest_minutes
    previous={'action':'study_replan','instruction':'將 DB03,04,05 安排到現在到今天早上九點，每次休息5分鐘'}
    combined=revision_instruction('改成每次休息2分鐘',previous)
    assert 'DB03,04,05' in combined and '今天早上九點' in combined
    assert rest_minutes(combined)==2
    assert revision_instruction('明天下午安排另一份報告',previous)=='明天下午安排另一份報告'

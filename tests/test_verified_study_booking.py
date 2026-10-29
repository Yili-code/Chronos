import asyncio
from copy import deepcopy
from datetime import timedelta
import pytest
from chronos import calendar_replan
from chronos.study_reflow import movable
from chronos.planning_holds import slots
from test_learning_calendar import setup, NOW
from test_task_focus import initialize


@pytest.mark.parametrize('case', ['valid','unlinked','wrong_task','wrong_token','wrong_time','wrong_event','pending','not_scheduled','creator','organizer','shared','recurring','calendar'])
def test_course_title_requires_verified_identity_and_all_original_safety_checks(setup,case):
    planner,target,calendar,_ = setup
    now,target,other,events = initialize(planner,target,calendar)
    event = deepcopy(events['event1']); event['summary'] = 'Operating Systems lecture 3'
    state = deepcopy(planner.get(other['id']))
    if case == 'unlinked': event.pop('extendedProperties')
    if case == 'wrong_task': event['extendedProperties']['private']['chronosTask'] = '999'
    if case == 'wrong_token': event['extendedProperties']['private']['chronosToken'] = 'other-token'
    if case == 'wrong_time': state['end'] = now.replace(hour=17).isoformat()
    if case == 'wrong_event': state['event_id'] = 'other-event'
    if case == 'pending': state['pending_reschedule'] = {'start':now.isoformat()}
    if case == 'not_scheduled': state['status'] = 'creating'
    if case == 'creator': event['creator']['email'] = 'other@example.com'
    if case == 'organizer': event['organizer'] = {'email':'other@example.com'}
    if case == 'shared': event['attendees'] = [{'email':'other@example.com'}]
    if case == 'recurring': event['recurringEventId'] = 'weekly'
    if case == 'calendar': event['_calendar_id'] = 'other'
    assert not movable(event,planner.settings,now)
    assert bool(movable(event,planner.settings,now,task_id=other['id'],study_state=state)) == (case == 'valid')


@pytest.mark.parametrize('change',['none','token_before_allow','shared_before_allow'])
def test_os_lecture_study_session_moves_through_real_focus_review_and_allow(setup,change):
    planner,target,calendar,_ = setup
    now,target,other,events = initialize(planner,target,calendar)
    events['event1']['summary'] = 'Operating Systems lecture 3'
    end = now.replace(hour=16,minute=7)
    events['event1']['end']['dateTime'] = end.isoformat()
    planner.update(other['id'],lambda s:{**s,'end':end.isoformat(),'minutes':67})
    planner.db.mutate_task_action_state('learning:day:'+now.date().isoformat(),lambda s:{**s,'session1':67})
    result = asyncio.run(calendar_replan.preview(planner,123,'下午的時間都用來完成 task 2',9001,now,focus_task=target,focus_window=(now.replace(hour=13,minute=0),now.replace(hour=18,minute=0))))
    assert isinstance(result,dict),result
    review = planner.db.get_task_action_state(calendar_replan.key(123))
    assert len(review['calendar_moves']) == 1
    assert review['calendar_moves'][0]['event_id'] == 'event1'
    calendar.move_event.assert_not_awaited()
    calendar_replan.bind(planner.db,123,50,result['messages'][0])
    if change == 'token_before_allow': events['event1']['extendedProperties']['private']['chronosToken'] = 'changed'
    if change == 'shared_before_allow': events['event1']['attendees'] = [{'email':'other@example.com'}]
    reply = asyncio.run(calendar_replan.callback(planner,123,50,review['token'],'allow',now))
    if change == 'none':
        assert isinstance(reply,dict),reply
        assert '下午已保留' in reply['messages'][0]['text']
        calendar.move_event.assert_awaited_once()
        state = planner.get(other['id'])
        from datetime import datetime
        assert datetime.fromisoformat(state['end'])-datetime.fromisoformat(state['start']) == timedelta(minutes=67)
        assert slots(planner.db)
    else:
        calendar.move_event.assert_not_awaited()
        assert not slots(planner.db)
    calendar.insert.assert_not_awaited()

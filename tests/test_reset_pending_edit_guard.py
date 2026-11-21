import asyncio

import pytest

from chronos import study_reset, event_reset
from test_learning_calendar import setup, NOW
from test_study_reset import initialize as study_setup
from test_event_reset import initialize as event_setup


@pytest.mark.parametrize('kind',['study','work'])
def test_bulk_rebuild_preserves_interrupted_booked_edit(setup,kind):
    if kind=='study':
        planner,events,output,task,*_=study_setup(setup)
    else:
        planner,events,_=event_setup(setup,'work')
        task=planner.db.create_task('Target work report',None,'work',NOW,{})
    planner.update(task['id'],lambda old:{**(old or {}),'status':(old or {}).get('status','queued'),
        'pending_reschedule':{'stage':'verifying','request_id':'existing'}})
    before=dict(events)
    result=asyncio.run(study_reset.run(planner,123,'rebuild all study',1,NOW) if kind=='study' else
                       event_reset.run(planner,123,'work','rebuild all work',1,NOW))
    assert '已重新安排好' not in result
    assert events==before
    planner.calendar.delete_event.assert_not_awaited()
    planner.calendar.insert.assert_not_awaited()
    assert planner.get(task['id'])['pending_reschedule']['stage']=='verifying'

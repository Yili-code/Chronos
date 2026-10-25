"""Previously verified Calendar writes cannot be assumed present on recovery."""
from chronos import study_reset as reset
from chronos.google_calendar import CalendarError
from test_learning_calendar import setup
from test_study_reset import initialize, rebuild


def test_rebuild_recovery_stops_if_a_verified_replacement_was_removed(setup):
    planner, events, *_ = initialize(setup)
    insert = planner.calendar.insert.side_effect
    async def fail_second(body):
        if events: raise CalendarError('Second insertion unavailable')
        return await insert(body)
    planner.calendar.insert.side_effect = fail_second
    assert '重排暫停' in rebuild(planner)
    state = planner.db.get_task_action_state(reset.KEY)
    assert len(state['inserted']) == 1
    removed = state['inserted'][0]
    del events[removed]
    calls = planner.calendar.insert.await_count
    planner.calendar.insert.side_effect = insert
    reply = rebuild(planner, update=2)
    assert '已重新安排好' not in reply
    assert removed not in events
    assert planner.calendar.insert.await_count == calls
    assert planner.db.get_task_action_state(reset.KEY)['status'] == 'partial'

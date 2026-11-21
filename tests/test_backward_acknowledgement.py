"""Backward confirmation must verify actual events, including uncertain recovery."""
from copy import deepcopy

import pytest

from chronos.backward_planning import key
from chronos.google_calendar import CalendarError
from test_backward_planning import setup, preview, approve
from test_deadline_reminders import store


@pytest.mark.parametrize('failure', ['missing', 'time', 'title', 'cancelled', 'unreadable'])
def test_backward_insert_requires_actual_event_readback(store, failure):
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner)
    insert = calendar.insert.side_effect
    async def incomplete(body):
        response = deepcopy(await insert(body))
        actual = events[body['id']]
        if failure == 'missing': del events[body['id']]
        if failure == 'time': actual['start']['dateTime'] = '2026-10-19T01:00:00+08:00'
        if failure == 'title': actual['summary'] = 'Different deliverable'
        if failure == 'cancelled': actual['status'] = 'cancelled'
        if failure == 'unreadable': calendar.event.side_effect = CalendarError('Readback unavailable')
        return response  # A successful response alone cannot prove current state.
    calendar.insert.side_effect = incomplete
    reply = approve(planner, token)
    assert 'Plan created.' not in reply
    saved = store.get_task_action_state(key(token))
    assert saved['status'] == 'partial'
    assert saved['events'] == {}
    assert calendar.insert.await_count == 1
    assert len(store.list_open_tasks()) == 4  # Durable partial work is retained.
    assert 'uncertain' in reply


def test_backward_recovery_does_not_recreate_a_verified_then_removed_event(store):
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner)
    insert = calendar.insert.side_effect
    async def fail_second(body):
        if events: raise CalendarError('Second insertion unavailable')
        return await insert(body)
    calendar.insert.side_effect = fail_second
    assert 'could not verify' in approve(planner, token)
    state = store.get_task_action_state(key(token))
    assert len(state['events']) == 1
    removed = next(iter(events))
    del events[removed]  # GET returns None for Google's 404/410 response.
    before = calendar.insert.await_count
    calendar.insert.side_effect = insert
    reply = approve(planner, token)
    assert 'Plan created.' not in reply
    assert removed not in events
    assert calendar.insert.await_count == before
    assert store.get_task_action_state(key(token))['status'] == 'partial'

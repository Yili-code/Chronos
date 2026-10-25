"""Entering Edit revokes approval of the old arrangement immediately."""
import asyncio
from copy import deepcopy

from chronos import calendar_replan, sequence_replan
from test_calendar_replan import initialize, proposal
from test_learning_calendar import setup, NOW
from test_sequence_replan import group, preview, NOW as SEQUENCE_NOW


def test_window_review_edit_revokes_allow_before_replacement(setup):
    planner, calendar, events = initialize(setup)
    state, _ = proposal(planner)
    before = deepcopy(events)
    asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'edit', NOW))
    assert planner.db.get_task_action_state(calendar_replan.key(123))['status'] == 'editing'
    for _ in range(2):
        reply = asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW))
        assert 'updated' not in reply.lower()
    assert events == before
    calendar.move_event.assert_not_awaited()
    assert planner.db.get_task_action_state(calendar_replan.key(123))['status'] == 'editing'
    asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'cancel', NOW))
    assert planner.db.get_task_action_state(calendar_replan.key(123))['status'] == 'cancelled'


def test_sequence_review_edit_revokes_allow_before_replacement(group):
    planner, calendar, events, add = group
    for n in (1, 2, 3): add(n)
    _, state = preview(group)
    before = deepcopy(events)
    asyncio.run(sequence_replan.callback(planner, 123, 50, state['token'], 'edit', SEQUENCE_NOW))
    assert planner.db.get_task_action_state(calendar_replan.key(123))['status'] == 'editing'
    for _ in range(2):
        reply = asyncio.run(sequence_replan.callback(planner, 123, 50, state['token'], 'allow', SEQUENCE_NOW))
        assert 'completed.' not in reply.lower()
    assert events == before
    calendar.move_event.assert_not_awaited()
    assert planner.db.get_task_action_state(calendar_replan.key(123))['status'] == 'editing'
    asyncio.run(sequence_replan.callback(planner, 123, 50, state['token'], 'cancel', SEQUENCE_NOW))
    assert planner.db.get_task_action_state(calendar_replan.key(123))['status'] == 'cancelled'


def test_failed_new_window_constraints_revoke_old_pending_allow(setup):
    from chronos.ai import AIError
    planner, calendar, events = initialize(setup)
    state, _ = proposal(planner)
    before = deepcopy(events)
    planner.ai.parse_replan.side_effect = AIError('Temporary provider outage')
    asyncio.run(calendar_replan.preview(planner, 123, 'Change the arrangement to tomorrow morning', 2, NOW))
    asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert events == before
    calendar.move_event.assert_not_awaited()


def test_failed_new_sequence_constraints_revoke_old_pending_allow(group):
    import pytest
    from chronos.ai import AIError
    planner, calendar, events, add = group
    for n in (1, 2, 3): add(n)
    _, state = preview(group)
    before = deepcopy(events)
    planner.ai.parse_sequence_replan.side_effect = AIError('Temporary provider outage')
    with pytest.raises(AIError):
        asyncio.run(sequence_replan.preview(planner, 123, 'Reschedule ISPM chapters 1-3 in order, only mornings.', 2, SEQUENCE_NOW))
    asyncio.run(sequence_replan.callback(planner, 123, 50, state['token'], 'allow', SEQUENCE_NOW))
    assert events == before
    calendar.move_event.assert_not_awaited()

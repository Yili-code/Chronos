"""Booked edit acknowledgement and concurrent-state regressions on both stores."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chronos.google_calendar import CalendarError
from chronos.task_actions import task_fields
from test_learning_calendar import NOW, setup


def booked(setup):
    planner, task, calendar, _ = setup
    start = NOW.replace(hour=14)
    state = {'status': 'scheduled', 'token': 'booked', 'event_id': 'existing',
             'start': start.isoformat(), 'end': (start + timedelta(minutes=60)).isoformat(),
             'minutes': 60, 'snapshot': task_fields(task)}
    planner.update(task['id'], lambda old: state)
    planner.db.mutate_task_action_state('learning:day:2026-10-19', lambda old: {'booked': 60})
    event = {'id': 'existing', 'etag': 'v1', '_calendar_id': planner.settings.calendar_id,
             'creator': {'email': planner.settings.calendar_account}, 'organizer': {'self': True},
             'start': {'dateTime': state['start']}, 'end': {'dateTime': state['end']},
             'extendedProperties': {'private': {'chronosTask': str(task['id']), 'chronosToken': 'booked'}}}
    calendar.event.side_effect = lambda _: deepcopy(event)
    calendar.agenda.side_effect = lambda *args: [deepcopy(event)]
    async def move(event_id, body, etag):
        assert event_id == event['id'] and etag == event['etag']
        event.update(deepcopy(body))
        event['etag'] = 'v2'
        return deepcopy(event)
    calendar.move_event.side_effect = move
    return planner, task, calendar, event, deepcopy(state)


@pytest.mark.parametrize('failure', ['unchanged', 'wrong_time', 'wrong_token', 'cancelled', 'unreadable', 'identity', 'malformed_time', 'naive_time'])
def test_booked_edit_requires_readback_before_success(setup, failure):
    planner, task, calendar, event, original = booked(setup)
    original_move = calendar.move_event.side_effect
    async def incomplete(event_id, body, etag):
        if failure == 'unchanged':
            return deepcopy(event)  # Successful transport, no requested mutation.
        await original_move(event_id, body, etag)
        if failure == 'wrong_time': event['end']['dateTime'] = '2026-10-19T16:00:00+08:00'
        if failure == 'wrong_token': event['extendedProperties']['private']['chronosToken'] = 'foreign'
        if failure == 'cancelled': event['status'] = 'cancelled'
        if failure == 'identity': event['id'] = 'another-event'
        if failure == 'malformed_time': event['end']['dateTime'] = 'unusable'
        if failure == 'naive_time': event['end']['dateTime'] = '2026-10-19T15:00:00'
        if failure == 'unreadable': calendar.event.side_effect = CalendarError('Readback unavailable')
        return deepcopy(event)
    calendar.move_event.side_effect = incomplete
    with pytest.raises(CalendarError):
        asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    saved = planner.get(task['id'])
    assert saved['start'] == original['start'] and saved['minutes'] == 60
    assert saved.get('pending_reschedule')
    assert planner.db.get_task_action_state('learning:day:2026-10-19') == {'booked': 60}
    assert planner.db.get_task(task['id'])['status'] == 'open'
    calendar.move_event.assert_awaited_once()


@pytest.mark.parametrize('when', ['parse', 'agenda', 'write'])
@pytest.mark.parametrize('change', ['complete', 'edit'])
def test_booked_edit_does_not_acknowledge_concurrent_task_change(setup, when, change):
    planner, task, calendar, event, original = booked(setup)
    def mutate():
        if change == 'complete': planner.db.complete_task(task['id'], NOW)
        else: planner.db.edit_task(task['id'], 'New scope', datetime.fromisoformat(task['due_at']), task['project'], task['timing'])
    async def parse(*args):
        if when == 'parse': mutate()
        return SimpleNamespace(actionable=True, timing='exact', start='2026-10-19T14:30:00+08:00',
                               minutes=30, progress_note=None)
    planner.ai = SimpleNamespace(parse_schedule_edit=AsyncMock(side_effect=parse))
    async def agenda(*args):
        if when == 'agenda': mutate()
        return [deepcopy(event)]
    calendar.agenda.side_effect = agenda
    original_move = calendar.move_event.side_effect
    async def move(*args):
        result = await original_move(*args)
        if when == 'write': mutate()
        return result
    calendar.move_event.side_effect = move
    try:
        reply = asyncio.run(planner.revise(task['id'], 'booked', 'Move it to 14:30 for half an hour', NOW))
    except CalendarError:
        reply = ''
    assert 'updated' not in reply.lower() and 'recovered' not in reply.lower()
    assert planner.get(task['id'])['start'] == original['start']
    assert planner.db.get_task_action_state('learning:day:2026-10-19') == {'booked': 60}
    if when == 'write':
        calendar.move_event.assert_awaited_once()
        assert planner.get(task['id']).get('pending_reschedule')
    else:
        calendar.move_event.assert_not_awaited()


@pytest.mark.parametrize('failure', ['lost_response', 'readback_timeout', 'local_interruption'])
def test_booked_edit_reconciles_once_after_uncertain_result(setup, monkeypatch, failure):
    import chronos.study_reschedule as module
    planner, task, calendar, event, original = booked(setup)
    move = calendar.move_event.side_effect
    read = calendar.event.side_effect
    record = module.record
    async def uncertain(*args):
        result = await move(*args)
        if failure == 'lost_response': raise CalendarError('Lost PATCH response')
        if failure == 'readback_timeout': calendar.event.side_effect = CalendarError('Readback timeout')
        return result
    calendar.move_event.side_effect = uncertain
    if failure == 'local_interruption':
        monkeypatch.setattr(module, 'record', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('Interrupted before local receipt')))
    with pytest.raises((CalendarError, RuntimeError)):
        asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    assert planner.get(task['id'])['start'] == original['start']
    assert planner.get(task['id']).get('pending_reschedule')
    calendar.event.side_effect = read
    monkeypatch.setattr(module, 'record', record)
    reply = asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    assert 'recovered' in reply
    saved = planner.get(task['id'])
    assert saved['start'] == event['start']['dateTime'] == '2026-10-19T14:30:00+08:00'
    assert saved['minutes'] == 30 and 'pending_reschedule' not in saved
    assert planner.db.get_task_action_state('learning:day:2026-10-19') == {'booked': 30}
    assert planner.db.get_task(task['id'])['status'] == 'open'
    calendar.move_event.assert_awaited_once()
    calendar.insert.assert_not_awaited()


def test_different_request_cannot_silently_retry_an_older_uncertain_edit(setup):
    planner, task, calendar, event, original = booked(setup)
    move = calendar.move_event.side_effect
    calendar.move_event.side_effect = CalendarError('PATCH failed before response; no effect in this simulation')
    with pytest.raises(CalendarError):
        asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    pending = deepcopy(planner.get(task['id'])['pending_reschedule'])
    calendar.move_event.side_effect = move
    reply = asyncio.run(planner.revise(task['id'], 'booked', '10/19 16:00 60', NOW))
    assert 'updated' not in reply.lower()
    assert calendar.move_event.await_count == 1
    assert event['start']['dateTime'] == original['start']
    assert planner.get(task['id'])['pending_reschedule'] == pending
    # Repeating the authorized original request reconciles and performs one move.
    reply = asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    assert 'updated' in reply.lower()
    assert calendar.move_event.await_count == 2
    assert event['start']['dateTime'] == '2026-10-19T14:30:00+08:00'

@pytest.mark.parametrize('text', ['10/19 16:00 60', 'cancel', '取消', '不要改了'])
def test_pending_edit_rejects_new_or_cancel_text_without_replaying_old_write(setup, text):
    planner, task, calendar, event, original = booked(setup)
    calendar.move_event.side_effect = CalendarError('Uncertain PATCH')
    with pytest.raises(CalendarError):
        asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    state = deepcopy(planner.get(task['id']))
    calendar.move_event.reset_mock()
    reply = asyncio.run(planner.revise(task['id'], 'booked', text, NOW))
    assert 'reconciliation' in reply
    calendar.move_event.assert_not_awaited()
    assert planner.get(task['id']) == state
    assert event['start']['dateTime'] == original['start']


def test_legacy_pending_edit_can_be_explicitly_retried(setup):
    planner, task, calendar, event, original = booked(setup)
    move = calendar.move_event.side_effect
    calendar.move_event.side_effect = CalendarError('Uncertain PATCH')
    with pytest.raises(CalendarError):
        asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    planner.update(task['id'], lambda old: {**old, 'pending_reschedule': {
        k: v for k, v in old['pending_reschedule'].items() if k != 'request_digest'}})
    calendar.move_event.side_effect = move
    reply = asyncio.run(planner.revise(task['id'], 'booked', 'retry', NOW))
    assert 'updated' in reply
    assert event['start']['dateTime'] == '2026-10-19T14:30:00+08:00'
    assert 'pending_reschedule' not in planner.get(task['id'])

@pytest.mark.parametrize('change', ['shared','recurring','instance','foreign_creator','unknown_owner','foreign_organizer','special'])
@pytest.mark.parametrize('when', ['before','after'])
def test_booked_edit_protects_shared_recurring_and_unowned_events(setup, change, when):
    planner, task, calendar, event, original = booked(setup)
    def change_ownership():
        if change == 'shared': event['attendees'] = [{'email':'participant@example.com'}]
        if change == 'recurring': event['recurrence'] = ['RRULE:FREQ=WEEKLY']
        if change == 'instance': event['recurringEventId'] = 'parent-series'
        if change == 'foreign_creator': event['creator'] = {'email':'another-owner@example.com'}
        if change == 'unknown_owner': event.pop('creator',None)
        if change == 'foreign_organizer': event['organizer'] = {'email':'another-owner@example.com'}
        if change == 'special': event['eventType'] = 'focusTime'
    move = calendar.move_event.side_effect
    if when == 'before': change_ownership()
    else:
        async def changed_after_write(*args):
            result = await move(*args)
            change_ownership()
            return result
        calendar.move_event.side_effect = changed_after_write
    with pytest.raises(CalendarError):
        asyncio.run(planner.revise(task['id'],'booked','10/19 14:30 30',NOW))
    assert planner.get(task['id'])['start'] == original['start']
    assert planner.db.get_task_action_state('learning:day:2026-10-19') == {'booked':60}
    if when == 'before': calendar.move_event.assert_not_awaited()
    else:
        calendar.move_event.assert_awaited_once()
        assert planner.get(task['id']).get('pending_reschedule')


def test_scheduler_waits_for_uncertain_booked_edit_before_checking_in(setup):
    planner, task, calendar, event, original = booked(setup)
    calendar.move_event.side_effect = CalendarError('Uncertain PATCH')
    with pytest.raises(CalendarError):
        asyncio.run(planner.revise(task['id'],'booked','10/19 14:30 30',NOW))
    saved = deepcopy(planner.get(task['id']))
    bot = planner.telegram
    bot.send_message.reset_mock()
    calendar.event.reset_mock()
    asyncio.run(planner.tick(NOW + timedelta(hours=10)))
    assert planner.get(task['id']) == saved
    assert planner.db.get_task(task['id'])['status'] == 'open'
    bot.send_message.assert_not_awaited()
    calendar.event.assert_not_awaited()

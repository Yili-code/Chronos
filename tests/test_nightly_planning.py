"""Nightly selection and approval boundaries on both persistence backends."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from test_learning_calendar import setup as base_setup, NOW
from test_system import system
from chronos import main
from chronos.google_calendar import CalendarError
from chronos.ai import AIError, NightlyPlanOutput, NightlySession

NIGHT = NOW.replace(hour=21)


@pytest.fixture
def setup(base_setup):
    planner, task, calendar, bot = base_setup
    ai = AsyncMock()
    async def allocate(tasks, windows, budget, history):
        sessions, remaining = [], budget
        candidates = iter(tasks)
        item = next(candidates, None)
        for window in windows:
            start, end = (NOW.fromisoformat(window[k]) for k in ('start', 'end'))
            while item and remaining >= 60 and start + timedelta(minutes=60) <= end and len(sessions) < 10:
                sessions.append(NightlySession(task_id=item['id'], start=start, end=start + timedelta(minutes=60),
                    estimated_minutes=60, reason='預計一小時可完成此工作。'))
                start += timedelta(minutes=60)
                remaining -= 60
                item = next(candidates, None)
        return NightlyPlanOutput(sessions=sessions)
    ai.plan_tomorrow.side_effect = allocate
    ai.estimate_duration.side_effect = AIError('No separate estimate in this fixture')
    planner.ai = ai
    return planner, task, calendar, bot


def add(planner, name, age, due=None):
    return planner.db.create_task(name, due, None, NOW - timedelta(days=age))


def test_whole_day_is_not_limited_to_four_tasks_and_includes_old_backlog(setup):
    planner, task, calendar, bot = setup
    newer = add(planner, 'Urgent but new', 1, NOW + timedelta(days=1))
    oldest = [add(planner, str(age), age) for age in (6, 5, 4, 3, 2)]
    result = asyncio.run(planner.nightly_proposals(NIGHT))
    assert result['nightly_task_ids'] == [task['id']] + [item['id'] for item in oldest]
    assert planner.get(oldest[0]['id'])['status'] == 'queued'
    assert planner.get(oldest[1]['id'])['status'] == 'queued'
    assert planner.get(task['id'])['status'] == 'proposed' and planner.get(newer['id']) is None
    assert planner.get(oldest[4]['id'])['status'] == 'queued'
    assert planner.get(oldest[0]['id'])['nightly_slot']['start'] >= (NIGHT + timedelta(days=1)).replace(hour=9).isoformat()
    assert bot.send_message.await_count == 1
    calendar.insert.assert_not_awaited()


def test_daily_retry_keeps_selection_and_does_not_resend(setup):
    planner, task, calendar, bot = setup
    first = asyncio.run(planner.nightly_proposals(NIGHT))
    add(planner, 'Arrived after selection', 10)
    again = asyncio.run(planner.nightly_proposals(NIGHT + timedelta(minutes=5)))
    assert first['nightly_task_ids'] == again['nightly_task_ids'] == [task['id']]
    assert bot.send_message.await_count == 1
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('status', ['scheduled', 'awaiting_feedback', 'postponed', 'rejected', 'backward_managed', 'queued'])
def test_existing_work_is_not_selected_or_reset(setup, status):
    planner, task, calendar, bot = setup
    old = {'status': status, 'token': 'existing', 'end': (NIGHT + timedelta(days=1)).isoformat()}
    planner.update(task['id'], lambda _: old)
    candidate = add(planner, 'Unplanned', 3)
    result = asyncio.run(planner.nightly_proposals(NIGHT))
    assert result['nightly_task_ids'] == [candidate['id']]
    assert planner.get(task['id']) == old
    calendar.insert.assert_not_awaited()


def test_active_approval_is_preserved_then_nightly_queue_advances_after_accept(setup):
    planner, task, calendar, bot = setup
    asyncio.run(planner.tick(NOW))
    active = planner.get(task['id'])
    next_task = add(planner, 'Old backlog', 10)
    asyncio.run(planner.nightly_proposals(NIGHT))
    assert planner.get(task['id']) == active
    assert planner.get(next_task['id'])['status'] == 'queued'
    assert bot.send_message.await_count == 1
    # Approve an actual fresh nightly proposal, retaining normal conflict checks.
    planner.update(task['id'], lambda old: {**old, 'status': 'rejected'})
    asyncio.run(planner.tick(NIGHT))
    proposal = planner.get(next_task['id'])
    assert proposal['status'] == 'proposed'
    response = asyncio.run(planner.callback('approve', next_task['id'], proposal['token'], proposal['message_id']))
    assert 'scheduled' in response.lower()
    assert calendar.insert.await_count == 1


def test_batch_continues_after_midnight_without_selecting_unrelated_work(setup):
    planner, task, calendar, bot = setup
    other = add(planner, 'Second', 1)
    asyncio.run(planner.nightly_proposals(NIGHT))
    active = planner.db.get_task_action_state('learning:proposal-queue')['active_task_id']
    planner.db.complete_task(active, NIGHT)
    asyncio.run(planner.tick(NIGHT + timedelta(hours=4)))
    remaining = task if active == other['id'] else other
    assert planner.get(remaining['id'])['status'] == 'proposed'
    planner.db.complete_task(remaining['id'], NIGHT)
    unrelated = add(planner, 'Not in batch', 10)
    result = asyncio.run(planner.tick(NIGHT + timedelta(hours=4, minutes=1)))
    assert result['quiet_hours'] is True
    assert planner.get(unrelated['id']) is None
    calendar.insert.assert_not_awaited()


def test_failure_retry_uses_saved_batch(setup):
    planner, task, calendar, bot = setup
    calendar.busy.side_effect = CalendarError('offline')
    with pytest.raises(CalendarError):
        asyncio.run(planner.nightly_proposals(NIGHT))
    assert planner.get(task['id']) is None
    calendar.busy.side_effect = None
    asyncio.run(planner.nightly_proposals(NIGHT + timedelta(minutes=1)))
    assert planner.get(task['id'])['status'] == 'proposed'
    assert bot.send_message.await_count == 1
    calendar.insert.assert_not_awaited()


def test_wrong_hour_disabled_and_empty_batch_have_no_proposals(setup):
    planner, task, calendar, bot = setup
    assert 'waiting_for' in asyncio.run(planner.nightly_proposals(NOW))
    assert planner.get(task['id']) is None
    planner.db.complete_task(task['id'], NOW)
    assert asyncio.run(planner.nightly_proposals(NIGHT))['nightly_task_ids'] == []
    planner.settings.enable_learning_calendar = False
    assert asyncio.run(planner.nightly_proposals(NIGHT)) == {'enabled': False}
    bot.send_message.assert_not_awaited()
    calendar.insert.assert_not_awaited()


def test_nightly_endpoint_requires_scheduler_secret(system, monkeypatch):
    client, _, _, _ = system
    run = AsyncMock(return_value={'nightly_task_ids': [1]})
    monkeypatch.setattr(main, 'run_nightly_planning', run)
    assert client.post('/internal/learning/nightly').status_code == 403
    run.assert_not_awaited()
    response = client.post('/internal/learning/nightly', headers={'X-Chronos-Scheduler-Secret': 'test-scheduler'})
    assert response.status_code == 200 and response.json()['nightly_task_ids'] == [1]



def test_next_date_selects_only_new_unplanned_tasks_and_ties_use_id(setup):
    planner, task, calendar, bot = setup
    first = asyncio.run(planner.nightly_proposals(NIGHT))
    a, b = add(planner, 'Same age A', 4), add(planner, 'Same age B', 4)
    completed = add(planner, 'Already done', 9)
    planner.db.complete_task(completed['id'], NIGHT)
    second = asyncio.run(planner.nightly_proposals(NIGHT + timedelta(days=1)))
    assert first['nightly_task_ids'] == [task['id']]
    assert second['nightly_task_ids'] == [task['id'], a['id'], b['id']]
    assert planner.get(completed['id']) is None
    assert bot.send_message.await_count == 2  # Expired, unbooked work is replanned for the new target date.
    calendar.insert.assert_not_awaited()


def test_nightly_transport_uncertainty_never_resends_or_books(setup):
    planner, task, calendar, bot = setup
    bot.send_message.side_effect = RuntimeError('connection lost')
    with pytest.raises(RuntimeError):
        asyncio.run(planner.nightly_proposals(NIGHT))
    assert planner.get(task['id'])['delivery'] == 'proposal_uncertain'
    bot.send_message.side_effect = None
    asyncio.run(planner.nightly_proposals(NIGHT + timedelta(minutes=1)))
    assert bot.send_message.await_count == 1
    calendar.insert.assert_not_awaited()



def seed_nightly(planner, task, status='queued'):
    from chronos.task_actions import task_fields
    start = (NIGHT + timedelta(days=1)).replace(hour=10, minute=30)
    value = {'status': status, 'token': 'old-allocation', 'snapshot': task_fields(task),
        'nightly_batch': NIGHT.date().isoformat(),
        'nightly_slot': {'start': start.isoformat(), 'end': (start + timedelta(minutes=90)).isoformat()},
        'nightly_overview': 'Obsolete 10:30 overview', 'nightly_reason': 'Original allocation',
        'requested_minutes': 90, 'duration_source': 'nightly_ai',
        'duration_estimate': {'estimated_minutes': 120}}
    planner.update(task['id'], lambda old: value)
    return value


def test_stale_nightly_allocation_searches_again_without_booking(setup):
    planner, task, calendar, bot = setup
    state = seed_nightly(planner, task)
    start = NOW.fromisoformat(state['nightly_slot']['start'])
    calendar.busy.return_value = [(start, start + timedelta(minutes=90))]
    asyncio.run(planner.tick(NIGHT))
    fresh = planner.get(task['id'])
    assert fresh['status'] == 'proposed'
    assert fresh['nightly_slot'] != state['nightly_slot']
    assert fresh['nightly_slot']['start'] == fresh['start']
    assert fresh['minutes'] == fresh['requested_minutes'] == 90
    assert fresh['duration_estimate']['estimated_minutes'] == 120
    assert not fresh.get('nightly_overview')
    assert 'calendar_conflict' in fresh['nightly_invalid_reasons']
    text, _ = planner.proposal_content(task, fresh)
    assert 'Obsolete' not in text
    calendar.insert.assert_not_awaited()
    asyncio.run(planner.callback('approve', task['id'], 'old-allocation', 50))
    calendar.insert.assert_not_awaited()


def test_failed_nightly_search_reports_real_horizon_without_inventing_deadline(setup):
    planner, task, calendar, bot = setup
    planner.db.edit_task(task['id'], task['title'], None, task['project'], {})
    task = planner.db.get_task(task['id'])
    seed_nightly(planner, task)
    calendar.busy.return_value = [(NIGHT, NIGHT + timedelta(days=8))]
    asyncio.run(planner.tick(NIGHT))
    state = planner.get(task['id'])
    assert state['status'] == 'no_slot'
    assert state['availability_search']['scope'] == 'seven_days'
    text, _ = planner.proposal_content(task, state)
    assert 'next 7 days' in text and '90-minute' in text
    assert 'before the deadline' not in text
    assert 'original allocation' in text.lower()
    calendar.insert.assert_not_awaited()


def test_nightly_conflict_at_approval_requeues_for_fresh_consent(setup):
    planner, task, calendar, bot = setup
    seed_nightly(planner, task)
    asyncio.run(planner.tick(NIGHT))
    first = planner.get(task['id'])
    start = NOW.fromisoformat(first['start'])
    calendar.busy.return_value = [(start, NOW.fromisoformat(first['end']))]
    reply = asyncio.run(planner.callback('approve', task['id'], first['token'], first['message_id']))
    assert 'fresh' in reply.lower()
    assert planner.get(task['id'])['status'] == 'queued'
    calendar.insert.assert_not_awaited()
    asyncio.run(planner.tick(NIGHT))
    fresh = planner.get(task['id'])
    assert fresh['status'] == 'proposed' and fresh['token'] != first['token']
    assert 'approved_at' not in fresh
    calendar.insert.assert_not_awaited()



@pytest.mark.parametrize('cause', ['daily_limit', 'pending_plan', 'elapsed', 'duration_changed', 'deadline', 'fixed_commitment'])
def test_replacement_preserves_allocation_constraints(setup, monkeypatch, cause):
    from chronos.nightly_planning import slot_allowed
    from chronos.task_actions import task_fields
    planner, task, calendar, bot = setup
    original = seed_nightly(planner, task)
    start = NOW.fromisoformat(original['nightly_slot']['start'])
    if cause == 'daily_limit':
        planner.db.mutate_task_action_state('learning:day:' + start.date().isoformat(), lambda old: {'used': 540})
    elif cause == 'pending_plan':
        other = add(planner, 'Other allocated work', 1)
        planner.update(other['id'], lambda old: {'status': 'queued', 'duration_source': 'nightly_ai',
            'nightly_batch': NIGHT.date().isoformat(), 'nightly_slot': dict(original['nightly_slot'])})
    elif cause == 'elapsed':
        original['nightly_slot'] = {'start': NOW.isoformat(), 'end': (NOW + timedelta(minutes=90)).isoformat()}
    elif cause == 'duration_changed':
        original['nightly_slot']['end'] = (start + timedelta(minutes=60)).isoformat()
    elif cause == 'deadline':
        planner.db.edit_task(task['id'], task['title'], NOW + timedelta(days=2), task['project'], {})
        task = planner.db.get_task(task['id'])
        original['snapshot'] = task_fields(task)
        original['nightly_slot'] = {'start': (start + timedelta(days=3)).isoformat(),
                                    'end': (start + timedelta(days=3, minutes=90)).isoformat()}
    else:
        # The last five minutes conflict only with the class preparation buffer.
        import chronos.learning_calendar as module
        monkeypatch.setattr(module, 'class_windows', lambda day, tz, policy=None:
            [(start.replace(hour=12, minute=10), start.replace(hour=13, minute=0))]
            if day == start.date() else [])
    planner.update(task['id'], lambda old: original)
    asyncio.run(planner.tick(NIGHT))
    fresh = planner.get(task['id'])
    assert cause in fresh['nightly_invalid_reasons']
    assert fresh['status'] == 'proposed'
    assert slot_allowed(planner, task, fresh, NIGHT, calendar.busy.return_value) is not None
    assert fresh['minutes'] == 90
    calendar.insert.assert_not_awaited()


def test_legacy_nightly_no_slot_is_recovered_once_without_duplicate_messages(setup):
    planner, task, calendar, bot = setup
    old = seed_nightly(planner, task, status='no_slot')
    old.update(minutes=90, delivery='proposal_sent', message_id=40)
    planner.update(task['id'], lambda state: old)
    start = NOW.fromisoformat(old['nightly_slot']['start'])
    calendar.busy.return_value = [(start, start + timedelta(minutes=90))]
    asyncio.run(planner.tick(NIGHT))
    state = planner.get(task['id'])
    assert state['status'] == 'proposed' and state['token'] != old['token']
    assert state['delivery'] == 'proposal_sent'
    count = bot.send_message.await_count
    asyncio.run(planner.tick(NIGHT + timedelta(minutes=5)))
    assert bot.send_message.await_count == count
    calendar.insert.assert_not_awaited()


def test_exhausted_nightly_search_does_not_retry_and_resend_every_tick(setup):
    planner, task, calendar, bot = setup
    seed_nightly(planner, task)
    calendar.busy.return_value = [(NIGHT, NIGHT + timedelta(days=8))]
    asyncio.run(planner.tick(NIGHT))
    state = planner.get(task['id'])
    assert state['status'] == 'no_slot' and state['availability_search']['scope'] == 'deadline'
    count = bot.send_message.await_count
    asyncio.run(planner.tick(NIGHT + timedelta(minutes=5)))
    assert bot.send_message.await_count == count
    assert planner.get(task['id'])['token'] == state['token']
    calendar.insert.assert_not_awaited()


def test_short_nightly_replacement_respects_protected_breaks(setup):
    from chronos.nightly_planning import slot_allowed
    planner, task, calendar, bot = setup
    old = seed_nightly(planner, task)
    start = NOW.fromisoformat(old['nightly_slot']['start'])
    old['requested_minutes'] = 10
    calendar.busy.return_value = [(start.replace(hour=9, minute=0), start.replace(hour=12, minute=0))]
    planner.update(task['id'], lambda state: old)
    asyncio.run(planner.tick(NIGHT))
    state = planner.get(task['id'])
    assert state['status'] == 'proposed'
    assert slot_allowed(planner, task, state, NIGHT, calendar.busy.return_value) is not None
    assert state['minutes'] == 10
    calendar.insert.assert_not_awaited()



def test_replacement_calendar_read_failure_never_becomes_no_capacity(setup):
    planner, task, calendar, bot = setup
    old = seed_nightly(planner, task)
    start = NOW.fromisoformat(old['nightly_slot']['start'])
    calendar.busy.side_effect = [[(start, start + timedelta(minutes=90))], CalendarError('calendar offline')]
    with pytest.raises(CalendarError, match='offline'):
        asyncio.run(planner.tick(NIGHT))
    assert planner.get(task['id'])['status'] == 'queued'
    bot.send_message.assert_not_awaited()
    calendar.insert.assert_not_awaited()


def test_default_auto_accept_books_nightly_session_without_preview(setup):
    planner, task, calendar, bot = setup
    planner.settings.learning_auto_accept = True
    asyncio.run(planner.nightly_proposals(NIGHT))
    assert planner.get(task['id'])['status'] == 'scheduled'
    calendar.insert.assert_awaited_once()
    assert 'Scheduled, Sir' in bot.send_message.await_args.args[1]
    assert 'Confirm schedule' not in str(bot.send_message.await_args.kwargs)

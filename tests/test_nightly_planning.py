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

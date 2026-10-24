import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import main
from chronos.automatic_planning import accept
from chronos.google_calendar import CalendarError
from chronos.settings import Settings
from chronos.task_actions import task_fields
from test_learning_calendar import setup, NOW
from test_system import system


def test_default_is_auto_accept():
    assert Settings(_env_file=None).learning_auto_accept is True


def enable(setup):
    planner, task, calendar, bot = setup
    planner.settings.learning_auto_accept = True
    return planner, task, calendar, bot


def test_default_books_then_notifies_without_proposal_or_accept_button(setup):
    planner, task, calendar, bot = enable(setup)
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    assert state['status'] == 'scheduled' and state['auto_accepted']
    calendar.insert.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.args[1]
    assert 'Scheduled, Sir' in text and task['title'] in text and 'proposed' not in text
    assert 'Confirm schedule' not in str(bot.send_message.await_args.kwargs)
    assert 'Adjust time' in str(bot.send_message.await_args.kwargs)
    asyncio.run(planner.tick(NOW))
    calendar.insert.assert_awaited_once()
    bot.send_message.assert_awaited_once()


@pytest.mark.parametrize('failure', ['rejected', 'uncertain'])
def test_receipt_failure_never_rebooks_and_unknown_delivery_is_not_retried(setup, failure):
    planner, task, calendar, bot = enable(setup)
    if failure == 'uncertain':
        bot.send_message.side_effect = TimeoutError()
    else:
        bot.send_message.return_value = {'ok': False}
    with pytest.raises(CalendarError):
        asyncio.run(planner.tick(NOW))
    assert planner.get(task['id'])['status'] == 'scheduled'
    bot.send_message.side_effect = None
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 51}}
    asyncio.run(planner.tick(NOW))
    calendar.insert.assert_awaited_once()
    assert bot.send_message.await_count == (1 if failure == 'uncertain' else 2)


def test_insert_timeout_reconciles_same_event_before_notifying(setup):
    planner, task, calendar, bot = enable(setup)
    created = {}
    async def insert(body):
        created.update(body)
        raise CalendarError('Response lost')
    calendar.insert.side_effect = insert
    with pytest.raises(CalendarError):
        asyncio.run(planner.tick(NOW))
    assert planner.get(task['id'])['status'] == 'creating'
    bot.send_message.assert_not_awaited()
    calendar.event.return_value = created
    asyncio.run(planner.tick(NOW))
    assert planner.get(task['id'])['status'] == 'scheduled'
    calendar.insert.assert_awaited_once()
    bot.send_message.assert_awaited_once()


@pytest.mark.parametrize('change', ['conflict', 'snapshot', 'budget', 'deadline'])
def test_fresh_validation_prevents_automatic_booking(setup, change):
    planner, task, calendar, bot = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    planner.settings.learning_auto_accept = True
    if change == 'conflict':
        calendar.busy.return_value = [(NOW.fromisoformat(state['start']), NOW.fromisoformat(state['end']))]
    elif change == 'snapshot':
        planner.update(task['id'], lambda old: {**old, 'snapshot': {}})
        state = planner.get(task['id'])
    elif change == 'budget':
        day = state['start'][:10]
        planner.db.mutate_task_action_state('learning:day:' + day, lambda old: {'other': 720})
    else:
        task = {**task, 'due_at': NOW.isoformat()}
    asyncio.run(accept(planner, task, state, NOW))
    calendar.insert.assert_not_awaited()
    assert planner.get(task['id'])['status'] == 'queued'


@pytest.mark.parametrize('exception', ['moves', 'late'])
def test_exceptions_keep_explicit_confirmation(setup, exception):
    planner, task, calendar, bot = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    if exception == 'moves':
        state['calendar_moves'] = [{'title': 'Other event', 'old_start': state['start'], 'old_end': state['end'],
                                    'start': state['start'], 'end': state['end']}]
    else:
        state.update(start=NOW.replace(hour=22).isoformat(), end=NOW.replace(hour=23).isoformat())
    state['delivery'] = 'proposal_pending'
    planner.update(task['id'], lambda old: state)
    planner.settings.learning_auto_accept = True
    assert asyncio.run(accept(planner, task, state, NOW)) is False
    calendar.insert.assert_not_awaited()


def test_owner_batch_advances_without_waiting_for_accept(setup):
    planner, task, calendar, bot = enable(setup)
    other = planner.db.create_task('Other work', None, None, NOW)
    occupied = []
    async def insert(body):
        occupied.append(tuple(NOW.fromisoformat(body[k]['dateTime']) for k in ('start', 'end')))
        return body
    calendar.insert.side_effect = insert
    calendar.busy.side_effect = lambda *args: list(occupied)
    planner.request_proposals([task['id'], other['id']], minutes=30)
    for _ in range(3):
        asyncio.run(planner.tick(NOW))
    assert all(planner.get(i)['status'] == 'scheduled' for i in [task['id'], other['id']])
    assert calendar.insert.await_count == 2 and len(set(occupied)) == 2
    assert occupied[0][1] <= occupied[1][0]


def test_booked_receipt_opens_adjustment_without_changing_the_event(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.learning_auto_accept = True
    config.enable_learning_calendar = True
    config.learning_enabled_since = '2020-01-01T00:00:00+08:00'
    monkeypatch.setattr(main, 'google_calendar', calendar)
    task = tasks.create('Read chapter')
    planner = main.learning_calendar()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 50}}
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 88201, 'callback_query': {'id': 'adjust',
        'data': f"learn:adjustbooked:{task['id']}:{state['token']}",
        'message': {'message_id': 50, 'chat': {'id': 123}}}}
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    assert main.db.get_task_action_state('learning:edit-chat:123')['mode'] == 'booked'
    assert client.post('/telegram/webhook', headers=headers, json={'update_id': 88202,
        'message': {'message_id': 61, 'chat': {'id': 123}, 'text': '/cancel'}}).status_code == 200
    assert not main.db.get_task_action_state('learning:edit-chat:123')
    assert planner.get(task['id'])['status'] == 'scheduled'
    calendar.insert.assert_awaited_once()

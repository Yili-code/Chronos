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


def test_scheduled_receipt_shows_weekday_without_redundant_minutes(setup):
    from chronos.automatic_planning import receipt
    planner, task, _, bot = setup
    state = {'status': 'scheduled', 'token': 'receipt', 'delivery': 'scheduled_pending', 'minutes': 120,
        'start': '2026-10-31T13:00:00+08:00', 'end': '2026-10-31T15:00:00+08:00'}
    planner.update(task['id'], lambda old: state)
    asyncio.run(receipt(planner, task, state))
    text = bot.send_message.await_args.args[1]
    assert text.endswith('10/31 (Sat) 13:00–15:00')
    assert '120 minutes' not in text


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
    assert '已安排' in text and task['title'] in text and 'proposed' not in text
    from datetime import datetime
    from chronos.task_timing import display_date
    start = datetime.fromisoformat(state['start'])
    assert display_date(start.date().isoformat()) in text
    assert 'minutes' not in text
    assert 'Confirm schedule' not in str(bot.send_message.await_args.kwargs)
    assert '調整時間' in str(bot.send_message.await_args.kwargs)
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


def test_overdue_pending_receipt_goes_directly_to_feedback(setup):
    planner, task, calendar, bot = setup
    token = 'a' * 32
    state = {'status': 'scheduled', 'token': token, 'delivery': 'scheduled_pending', 'minutes': 30,
             'start': (NOW - timedelta(hours=1)).isoformat(), 'end': (NOW - timedelta(minutes=30)).isoformat(),
             'event_id': 'event', 'message_id': 40, 'snapshot': task_fields(task)}
    planner.update(task['id'], lambda old: state)
    calendar.event.return_value = {'end': {'dateTime': state['end']},
        'extendedProperties': {'private': {'chronosToken': token}}}
    asyncio.run(planner.tick(NOW))
    assert planner.get(task['id'])['status'] == 'awaiting_feedback'
    bot.send_message.assert_awaited_once()
    assert '這次任務進度' in bot.send_message.await_args.args[1]
    assert '已安排' not in bot.send_message.await_args.args[1]


def test_feedback_replaces_old_schedule_only_after_delivery_and_retries_cleanup(setup):
    planner, task, calendar, bot = setup
    state = {'status': 'awaiting_feedback', 'token': 'a' * 32, 'delivery': 'feedback_pending', 'minutes': 30,
             'start': NOW.isoformat(), 'end': (NOW + timedelta(minutes=30)).isoformat(),
             'superseded_schedule_id': 40, 'message_id': 40}
    planner.update(task['id'], lambda old: state)
    bot.send_message.return_value = {'ok': False}
    with pytest.raises(CalendarError):
        asyncio.run(planner.deliver_feedback(task, state))
    bot.request.assert_not_awaited()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 51}}
    bot.request.return_value = {'ok': False, 'error_code': 500}
    asyncio.run(planner.deliver_feedback(task, planner.get(task['id'])))
    assert planner.get(task['id'])['superseded_schedule_id'] == 40
    bot.request.return_value = {'ok': True}
    asyncio.run(planner.deliver_feedback(task, planner.get(task['id'])))
    assert bot.send_message.await_count == 2
    assert planner.get(task['id'])['superseded_schedule_id'] is None
    bot.request.assert_awaited_with('deleteMessage', {'chat_id': planner.settings.telegram_chat_id, 'message_id': 40})


def test_mail_schedule_and_feedback_have_compact_titles_full_time_and_source(setup, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    from chronos.automatic_planning import receipt
    planner, task, calendar, bot = setup
    task = {**task, 'title': 'Read email: ' + 'Networking ' * 25,
            'timing': {'source_text': 'Gmail: https://mail.google.com/mail/u/owner@example.com/#all/ab12'}}
    state = {'status': 'scheduled', 'token': 'a' * 32, 'delivery': 'scheduled_pending', 'minutes': 30,
             'start': NOW.isoformat(), 'end': (NOW + timedelta(minutes=30)).isoformat()}
    planner.update(task['id'], lambda old: state)
    asyncio.run(receipt(planner, task, state))
    scheduled = bot.send_message.await_args.args[1]
    feedback, buttons = planner.feedback_content(task, state)
    for text in [scheduled, feedback]:
        assert 'Read email:' not in text and 'Sir' not in text
        assert '郵件閱讀｜' in text and '…' in text
        assert '09:00–09:30' in text and '開啟郵件</a>' in text
    assert [b['text'] for b in buttons] == ['已完成', '完成一部分', '還沒開始']


def test_mail_schedule_receipt_delegates_expiry_and_preserves_legacy_recovery(setup, monkeypatch):
    import time
    from chronos.automatic_planning import receipt, expire_mail_receipts
    from chronos.mail_links import mail_url
    timer = AsyncMock()
    monkeypatch.setattr(asyncio, 'sleep', timer)
    planner, task, calendar, bot = setup
    task = {**task, 'timing': {'source_text': 'Gmail: ' + mail_url({'id': 'ab12'}, 'owner@example.com')}}
    state = {'status': 'scheduled', 'token': 'a' * 32, 'delivery': 'scheduled_pending', 'minutes': 30,
             'start': NOW.isoformat(), 'end': (NOW + timedelta(minutes=30)).isoformat()}
    planner.update(task['id'], lambda old: state)
    asyncio.run(receipt(planner, task, state))
    assert bot.send_message.await_args.kwargs['requires_response'] is False
    assert planner.get(task['id'])['status'] == 'scheduled'
    assert not planner.db.get_task_action_state('learning:mail-schedule-notices')
    planner.db.mutate_task_action_state('learning:mail-schedule-notices', lambda old: {'52': time.time()-1})
    bot.request.return_value = {'ok': False, 'error_code': 500}
    asyncio.run(expire_mail_receipts(planner, wait=True))
    assert planner.db.get_task_action_state('learning:mail-schedule-notices')
    bot.request.return_value = {'ok': True}
    asyncio.run(expire_mail_receipts(planner, wait=True))
    asyncio.run(receipt(planner, task, planner.get(task['id'])))
    assert not planner.db.get_task_action_state('learning:mail-schedule-notices')
    bot.send_message.assert_awaited_once()
    calendar.insert.assert_not_awaited()

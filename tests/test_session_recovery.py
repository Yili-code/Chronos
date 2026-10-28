import asyncio
from datetime import timedelta

import pytest

from chronos import session_recovery, main
from chronos.task_actions import task_fields
from test_learning_calendar import setup, NOW
from test_system import system


def missed(setup, reason=""):
    planner, task, calendar, bot = setup
    state = {'token': 'a' * 32, 'status': 'awaiting_feedback', 'snapshot': task_fields(task),
             'start': NOW.isoformat(), 'end': (NOW + timedelta(hours=1)).isoformat(),
             'minutes': 60, 'message_id': 50, 'delivery': 'feedback_sent'}
    planner.update(task['id'], lambda old: state)
    result = planner.feedback(task, state['token'], 'skipped', 0, reason)
    session_recovery.bind(planner, 51, result['messages'][0])
    return planner, task, calendar, bot, state['token'], result


def action(planner, task, token, name, message=51):
    return asyncio.run(planner.callback(name, task['id'], token, message))


def test_nonstart_asks_reason_then_replans_only_after_owner_choice(setup):
    planner, task, calendar, bot, token, result = missed(setup)
    assert 'What got in the way' in result['messages'][0]['text']
    assert planner.get(task['id'])['status'] == 'awaiting_resolution'
    assert len(planner.db.list_open_tasks()) == 1
    asyncio.run(planner.tick(NOW))
    calendar.insert.assert_not_awaited()
    assert planner.get(task['id'])['status'] == 'awaiting_resolution'
    result = action(planner, task, token, 'rload')
    assert 'Too much planned' in result['messages'][0]['text']
    session_recovery.bind(planner, 52, result['messages'][0])
    assert 'already' in action(planner, task, token, 'rshort')
    reply = action(planner, task, token, 'rshort', 52)
    assert 'confirmation' in reply
    assert planner.get(task['id'])['status'] == 'queued'
    assert planner.get(task['id'])['requested_minutes'] == 30
    history = planner.db.get_task_action_state('learning:history:' + token)
    assert history['recovery']['reason'] == 'Too much planned'
    assert history['recovery']['decision'] == 'rshort'
    asyncio.run(planner.tick(NOW))
    new = planner.get(task['id'])
    assert new['status'] == 'proposed' and new['minutes'] == 30
    assert new['recovery']['missed_count'] == 1
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('decision', ['rdefer', 'rconfirm'])
def test_pause_and_stop_preserve_task_deadline_and_calendar(setup, decision):
    planner, task, calendar, bot, token, result = missed(setup, 'No longer needed')
    if decision == 'rconfirm':
        result = action(planner, task, token, 'rstop')
        assert 'Confirm stop' in str(result)
        assert planner.get(task['id'])['status'] == 'awaiting_resolution'
        session_recovery.bind(planner, 52, result['messages'][0])
    reply = action(planner, task, token, decision, 52 if decision == 'rconfirm' else 51)
    assert 'task remains open' in reply
    assert planner.get(task['id'])['status'] == 'postponed'
    assert task_fields(planner.db.list_open_tasks()[0]) == task_fields(task)
    asyncio.run(planner.tick(NOW + timedelta(days=1)))
    calendar.insert.assert_not_awaited()
    calendar.patch.assert_not_awaited()


def test_repeated_nonstart_and_deadline_risk_are_visible(setup):
    planner, task, calendar, bot, token, _ = missed(setup, 'Forgot')
    state = planner.get(task['id'])
    state.update(status='awaiting_feedback', token='b' * 32)
    planner.update(task['id'], lambda old: state)
    result = planner.feedback(task, state['token'], 'skipped', 0)
    text = result['messages'][0]['text']
    assert '2 reported sessions' in text and 'smaller first step' in text
    assert 'Deadline:' in text
    assert len(planner.db.list_open_tasks()) == 1
    assert 'already' in planner.feedback(task, state['token'], 'skipped', 0)
    assert planner.get(task['id'])['recovery']['missed_count'] == 2


def test_unknown_outcome_gets_only_one_reminder_without_assuming_failure(setup):
    planner, task, calendar, bot = setup
    state = {'status': 'awaiting_feedback', 'token': 'a' * 32, 'snapshot': task_fields(task),
             'message_id': 50, 'minutes': 30, 'start': NOW.isoformat(),
             'end': NOW.isoformat(), 'feedback_requested_at': NOW.isoformat(), 'delivery': 'feedback_sent'}
    planner.update(task['id'], lambda old: state)
    for offset in [0, 1, 1, 2]:
        asyncio.run(planner.tick(NOW + timedelta(days=offset)))
    bot.send_message.assert_awaited_once()
    assert '還沒收到這次的進度' in bot.send_message.await_args.args[1]
    assert planner.get(task['id'])['status'] == 'awaiting_feedback'
    assert 'feedback' not in planner.get(task['id'])
    assert len(planner.db.list_open_tasks()) == 1
    calendar.insert.assert_not_awaited()


def test_changed_task_and_early_confirm_cannot_apply_decisions(setup):
    planner, task, calendar, bot, token, _ = missed(setup)
    result = action(planner, task, token, 'rconfirm')
    assert isinstance(result, dict)
    assert planner.get(task['id'])['status'] == 'awaiting_resolution'
    planner.db.complete_task(task['id'], NOW)
    assert 'no longer open' in action(planner, task, token, 'rdefer')
    calendar.insert.assert_not_awaited()


def test_overview_reopens_recovery_but_old_token_does_not(setup):
    planner, task, calendar, bot, token, _ = missed(setup)
    assert 'learning_recovery' in action(planner, task, token, 'recover', 999)['messages'][0]
    assert 'already' in action(planner, task, 'b' * 32, 'recover', 999)


def test_other_reason_is_escaped_and_recorded(setup):
    planner, task, calendar, bot, token, _ = missed(setup)
    result = session_recovery.respond(planner, task, token, 'rwrite', NOW, reason='<busy> & tired')
    assert '&lt;busy&gt; &amp; tired' in result['messages'][0]['text']
    assert planner.get(task['id'])['recovery']['reason'] == '<busy> & tired'
    assert planner.db.get_task_action_state('learning:history:' + token)['recovery']['reason'] == '<busy> & tired'


def test_automatic_recovery_acknowledges_next_step_without_queue_jargon(setup):
    planner, task, _, _, token, _ = missed(setup, 'An interruption')
    planner.settings.learning_auto_accept = True
    reply = action(planner, task, token, 'ragain')
    assert reply == 'I’ll find another time and let you know when it’s scheduled.'
    assert 'Queued' not in reply and 'task(s)' not in reply
    assert planner.get(task['id'])['status'] == 'queued'


@pytest.mark.parametrize('outcome', ['rejected', 'uncertain'])
def test_unanswered_reminder_retries_only_known_rejection(setup, outcome):
    from chronos.google_calendar import CalendarError
    planner, task, calendar, bot = setup
    state = {'status': 'awaiting_feedback', 'token': 'a' * 32, 'snapshot': task_fields(task),
             'minutes': 30, 'start': NOW.isoformat(), 'end': NOW.isoformat(), 'delivery': 'feedback_sent'}
    planner.update(task['id'], lambda old: state)
    if outcome == 'uncertain':
        bot.send_message.side_effect = TimeoutError('Unknown delivery')
    else:
        bot.send_message.return_value = {'ok': False}
    with pytest.raises(CalendarError):
        asyncio.run(planner.tick(NOW + timedelta(days=1)))
    state = planner.get(task['id'])
    assert state['delivery'] == ('feedback_uncertain' if outcome == 'uncertain' else 'feedback_pending')
    bot.send_message.side_effect = None
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 60}}
    asyncio.run(planner.tick(NOW + timedelta(days=2)))
    assert bot.send_message.await_count == (1 if outcome == 'uncertain' else 2)
    assert planner.get(task['id'])['status'] == 'awaiting_feedback'


def test_shortest_block_is_not_lengthened_by_shorter_choice(setup):
    planner, task, calendar, bot, token, _ = missed(setup, 'Too much planned')
    planner.update(task['id'], lambda old: {**old, 'minutes': 10})
    result = action(planner, task, token, 'rshort')
    assert isinstance(result, dict)
    assert planner.get(task['id'])['status'] == 'awaiting_resolution'
    assert 'Try a shorter' not in str(result)


@pytest.mark.parametrize('reply_style', ['reply', 'plain'])
@pytest.mark.parametrize('cleanup_failure', [False, True])
def test_recovery_webhook_binds_cards_and_clears_free_text_context(system, setup, monkeypatch, reply_style, cleanup_failure):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = '2020-01-01T00:00:00+08:00'
    monkeypatch.setattr(main, 'google_calendar', calendar)
    task = tasks.create('Read chapter')
    planner = main.learning_calendar()
    planner.update(task['id'], lambda old: {'status': 'awaiting_feedback', 'token': 'a' * 32,
        'snapshot': task_fields(task), 'minutes': 60, 'start': NOW.isoformat(), 'end': NOW.isoformat(), 'message_id': 40})
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 50}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    def callback(action, update, message):
        payload = {'update_id': update, 'callback_query': {'id': str(update),
            'data': f"learn:{action}:{task['id']}:" + 'a' * 32,
            'message': {'message_id': message, 'chat': {'id': 123}}}}
        return client.post('/telegram/webhook', headers=headers, json=payload)
    assert callback('skipped', 88001, 40).status_code == 200
    assert callback('skipped', 88001, 40).status_code == 200
    assert planner.get(task['id'])['recovery']['missed_count'] == 1
    assert planner.get(task['id'])['message_id'] == 50
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 51}}
    assert callback('rwrite', 88002, 50).status_code == 200
    assert main.db.get_task_action_state('learning:edit-chat:123')['mode'] == 'recovery'
    assert main.db.get_task_action_state('learning:edit-chat:123')['prompt_message_id'] == 51
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 52}}
    message = {'message_id': 61, 'chat': {'id': 123}, 'text': 'An urgent interruption'}
    if reply_style == 'reply':
        message['reply_to_message'] = {'message_id': 51, 'chat': {'id': 123}, 'text': 'What got in the way?'}
    def request(method, data):
        if method == 'deleteMessage' and data.get('message_id') == 51 and cleanup_failure:
            return {'ok': False, 'error_code': 500}
        return {'ok': True}
    bot.request.side_effect = request
    payload = {'update_id': 88003, 'message': message}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == (502 if cleanup_failure else 200)
    assert planner.get(task['id'])['recovery']['stage'] == 'decision'
    assert not main.db.get_task_action_state('learning:edit-chat:123')
    sends = bot.send_message.await_count
    bot.request.side_effect = None
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert bot.send_message.await_count == sends
    deletes = [c.args[1]['message_id'] for c in bot.request.await_args_list if c.args[0] == 'deleteMessage']
    assert 51 in deletes and 61 not in deletes and 52 not in deletes
    assert callback('rdefer', 88004, 52).status_code == 200
    assert planner.get(task['id'])['status'] == 'postponed'
    assert len(tasks.list_open()) == 1
    calendar.insert.assert_not_awaited()
    main.ai.classify_intent.assert_not_awaited()

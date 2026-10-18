import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import calendar_replan as module
from chronos.ai import ReplanOutput
from chronos.google_calendar import CalendarError
from test_learning_calendar import setup, NOW
from test_system import system


def initialize(setup):
    planner, task, calendar, bot = setup
    planner.ai = AsyncMock()
    planner.ai.parse_replan.return_value = ReplanOutput(actionable=True,
        start=NOW.isoformat(), end=NOW.replace(hour=12).isoformat(),
        destination_start=NOW.replace(hour=14).isoformat())
    event = {'id': 'personal', 'etag': 'v1', 'summary': 'Read <book>', '_calendar_id': 'primary',
        'creator': {'email': 'owner@example.com'}, 'organizer': {'self': True},
        'start': {'dateTime': NOW.replace(hour=10).isoformat()},
        'end': {'dateTime': NOW.replace(hour=11).isoformat()}}
    events = {'personal': event}
    calendar.agenda.side_effect = lambda *args: list(deepcopy(events).values())
    calendar.event.side_effect = lambda event_id: deepcopy(events.get(event_id))
    async def move(event_id, body, etag):
        if events[event_id]['etag'] != etag:
            raise CalendarError('Version changed')
        events[event_id].update(body)
        events[event_id]['etag'] = 'v2'
        return deepcopy(events[event_id])
    calendar.move_event.side_effect = move
    return planner, calendar, events


def proposal(planner, update=1):
    result = asyncio.run(module.preview(planner, 123, '清空今天現在到中午', update, NOW))
    assert isinstance(result, dict), result
    module.bind(planner.db, 123, 50, result['messages'][0])
    return planner.db.get_task_action_state(module.key(123)), result


def test_preview_then_allow_is_idempotent(setup):
    planner, calendar, events = initialize(setup)
    state, result = proposal(planner)
    assert 'Read &lt;book&gt;' in result['messages'][0]['text']
    assert [x['text'] for x in result['messages'][0]['reply_markup']['inline_keyboard'][0]] == ['Allow', 'Edit', 'Cancel']
    calendar.move_event.assert_not_awaited()
    reply = asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert 'Calendar updated' in reply
    assert events['personal']['start']['dateTime'] == state['calendar_moves'][0]['start']
    asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert calendar.move_event.await_count == 1


@pytest.mark.parametrize('action', ['cancel', 'edit'])
def test_cancel_edit_do_not_move_and_replacement_invalidates_old_allow(setup, action):
    planner, calendar, _ = initialize(setup)
    state, _ = proposal(planner)
    asyncio.run(module.callback(planner, 123, 50, state['token'], action, NOW))
    calendar.move_event.assert_not_awaited()
    replacement, _ = proposal(planner, 2)
    reply = asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert 'no longer active' in reply
    assert replacement['token'] != state['token']
    calendar.move_event.assert_not_awaited()


@pytest.mark.parametrize('change', ['version', 'destination', 'window', 'expiry', 'wrong_card'])
def test_changed_conditions_do_not_write(setup, change):
    planner, calendar, events = initialize(setup)
    state, _ = proposal(planner)
    now, message = NOW, 50
    if change == 'version':
        events['personal']['etag'] = 'external'
    elif change in {'destination', 'window'}:
        busy = deepcopy(events['personal'])
        busy.update(id='fixed', recurrence=['weekly'])
        if change == 'destination':
            busy['start']['dateTime'] = state['calendar_moves'][0]['start']
            busy['end']['dateTime'] = state['calendar_moves'][0]['end']
        events['fixed'] = busy
    elif change == 'expiry':
        now += timedelta(hours=3)
    else:
        message = 51
    asyncio.run(module.callback(planner, 123, message, state['token'], 'allow', now))
    calendar.move_event.assert_not_awaited()


@pytest.mark.parametrize('kind', ['recurring', 'shared', 'ongoing', 'secondary'])
def test_fixed_activity_blocks_whole_preview(setup, kind):
    planner, calendar, events = initialize(setup)
    event = events['personal']
    if kind == 'recurring': event['recurrence'] = ['weekly']
    if kind == 'shared': event['attendees'] = [{'email': 'other@example.com'}]
    if kind == 'ongoing': event['start']['dateTime'] = (NOW-timedelta(minutes=5)).isoformat()
    if kind == 'secondary': event['_calendar_id'] = 'other@example.com'
    result = asyncio.run(module.preview(planner, 123, 'free window', 1, NOW))
    assert '不能自動移動' in result
    calendar.move_event.assert_not_awaited()


def test_write_timeout_recovers_without_duplicate_move(setup):
    planner, calendar, events = initialize(setup)
    state, _ = proposal(planner)
    original = calendar.move_event.side_effect
    async def uncertain(*args):
        await original(*args)
        raise CalendarError('Timeout after write')
    calendar.move_event.side_effect = uncertain
    assert 'paused' in asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert 'updated' in asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert calendar.move_event.await_count == 1


def test_invalid_ai_time_cannot_write(setup):
    planner, calendar, _ = initialize(setup)
    planner.ai.parse_replan.return_value = ReplanOutput(actionable=True, start='bad', end=None, destination_start=None)
    result = asyncio.run(module.preview(planner, 123, 'bad', 1, NOW))
    assert '未變更' in result
    calendar.move_event.assert_not_awaited()

def test_linked_study_move_updates_budget_and_preserves_deadline(setup):
    planner, calendar, events = initialize(setup)
    task = planner.db.list_open_tasks()[0]
    event = events['personal']
    event['extendedProperties'] = {'private': {'chronosTask': str(task['id']), 'chronosToken': 'study'}}
    planner.update(task['id'], lambda old: {'status': 'scheduled', 'token': 'study', 'event_id': event['id'],
        'start': event['start']['dateTime'], 'end': event['end']['dateTime'], 'minutes': 60})
    planner.db.mutate_task_action_state('learning:day:' + NOW.date().isoformat(), lambda old: {'study': 60})
    state, _ = proposal(planner)
    assert state['calendar_moves'][0]['task_id'] == task['id']
    assert 'updated' in asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert planner.get(task['id'])['start'] == state['calendar_moves'][0]['start']
    assert planner.db.list_open_tasks()[0]['due_at'] == task['due_at']
    assert sum(planner.db.get_task_action_state('learning:day:' + NOW.date().isoformat()).values()) == 60


def test_edit_retains_window_and_requires_new_allow(setup):
    planner, calendar, events = initialize(setup)
    state, _ = proposal(planner)
    asyncio.run(module.callback(planner, 123, 50, state['token'], 'edit', NOW))
    planner.ai.parse_replan.return_value.destination_start = (NOW + timedelta(days=1)).isoformat()
    updated, _ = proposal(planner, 2)
    assert planner.ai.parse_replan.await_args.args[2]['start'] == state['start']
    assert updated['calendar_moves'][0]['start'][:10] != state['calendar_moves'][0]['start'][:10]
    calendar.move_event.assert_not_awaited()


def test_webhook_routes_preview_edit_cancel_and_deduplicates(system, monkeypatch):
    from chronos import main
    client, tasks, bot, config = system
    config.enable_learning_calendar = True
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 50}}
    preview_mock = AsyncMock(return_value={'messages': [{'text': 'Review', 'replan_token': 'a'*32}]})
    monkeypatch.setattr(module, 'preview', preview_mock)
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 90500, 'message': {'message_id': 49, 'chat': {'id': 123}, 'text': '/replan 清空今天到兩點'}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert preview_mock.await_count == 1
    assert bot.send_message.await_count == 1

    state = {'token': 'a'*32, 'status': 'pending', 'message_id': 50, 'chat_id': 123,
             'expires_at': (module.datetime.now(config.tz) + timedelta(hours=2)).isoformat()}
    main.db.mutate_task_action_state(module.key(123), lambda old: state)
    payload = {'update_id': 90501, 'callback_query': {'id': 'edit', 'data': 'replan:edit:' + 'a'*32,
        'message': {'message_id': 50, 'chat': {'id': 123}, 'text': 'Review'}}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert main.db.get_task_action_state(module.key(123))['status'] == 'editing'
    payload = {'update_id': 90502, 'message': {'message_id': 51, 'chat': {'id': 123}, 'text': '改排到明天以後'}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert preview_mock.await_count == 2
    payload['update_id'] = 90503
    payload['message']['text'] = '/cancel'
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert main.db.get_task_action_state(module.key(123))['status'] == 'cancelled'


def test_replan_calls_shared_ai_transport_and_validates_schema(monkeypatch):
    import json
    import httpx
    from chronos.ai import ExternalAI
    from test_ai import config, mock_provider
    def handler(request):
        payload = json.loads(request.content)
        assert 'destination_start' in payload['generationConfig']['responseJsonSchema']['properties']
        data = {'actionable': True, 'start': NOW.isoformat(),
                'end': NOW.replace(hour=14).isoformat(), 'destination_start': NOW.replace(hour=14).isoformat()}
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(data)}]}}]})
    mock_provider(monkeypatch, handler)
    result = asyncio.run(ExternalAI(config()).parse_replan('清空到下午兩點', NOW))
    assert result.actionable and result.end == NOW.replace(hour=14).isoformat()

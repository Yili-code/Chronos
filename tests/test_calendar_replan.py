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
    assert 'cannot be moved automatically' in result
    calendar.move_event.assert_not_awaited()


def test_fixed_activity_does_not_block_other_reviewed_moves(setup):
    planner, calendar, events = initialize(setup)
    fixed = deepcopy(events['personal'])
    fixed.update(id='fixed', etag='fixed-v1', summary='Shared class', recurrence=['weekly'])
    fixed['start']['dateTime'] = NOW.replace(hour=10, minute=30).isoformat()
    fixed['end']['dateTime'] = NOW.replace(hour=11, minute=30).isoformat()
    events['fixed'] = fixed

    result = asyncio.run(module.preview(planner, 123, 'free window', 2, NOW))

    assert isinstance(result, dict), result
    text = result['messages'][0]['text']
    assert 'Still occupied' in text and 'Shared class' in text and 'Read &lt;book&gt;' in text
    module.bind(planner.db, 123, 50, result['messages'][0])
    state = planner.db.get_task_action_state(module.key(123))
    assert len(state['blocked_events']) == 1
    assert len(state['calendar_moves']) == 1
    assert 'updated' in asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert calendar.move_event.await_count == 1


def test_changed_fixed_activity_invalidates_partial_review(setup):
    planner, calendar, events = initialize(setup)
    fixed = deepcopy(events['personal'])
    fixed.update(id='fixed', etag='fixed-v1', summary='Shared class', recurrence=['weekly'])
    events['fixed'] = fixed
    result = asyncio.run(module.preview(planner, 123, 'free window', 3, NOW))
    module.bind(planner.db, 123, 50, result['messages'][0])
    state = planner.db.get_task_action_state(module.key(123))
    events['fixed']['etag'] = 'changed'

    reply = asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', NOW))

    assert 'Nothing changed' in reply
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
    assert '沒有調整行程' in result
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
    bot.request.assert_awaited_with('deleteMessage', {'chat_id':123, 'message_id':50})


@pytest.mark.parametrize('ordered', [False, True])
def test_cancel_deletes_only_bound_preview_pages_and_retries_cleanup(system, ordered):
    from chronos import main
    client, tasks, bot, config = system
    config.enable_learning_calendar = True
    state = {'token':'b'*32, 'status':'pending', 'chat_id':123, 'message_id':50,
             'preview_message_ids':[40,50], 'ordered_sequence':ordered,
             'expires_at':(module.datetime.now(config.tz)+timedelta(hours=2)).isoformat()}
    main.db.mutate_task_action_state(module.key(123),lambda old:state)
    bot.send_message.return_value={'ok':True,'result':{'message_id':60}}
    bot.request.side_effect=[{'ok':True}, {'ok':False,'error_code':500},
                             {'ok':False,'error_code':400,'description':'Bad Request: message to delete not found'}, {'ok':True}]
    payload={'update_id':90600,'callback_query':{'id':'cancel','data':'replan:cancel:'+'b'*32,
             'message':{'message_id':50,'chat':{'id':123},'text':'Review'}}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==502
    assert main.db.get_task_action_state(module.key(123))['status']=='cancelled'
    # A newer review must not change the cleanup targets saved for this update.
    main.db.mutate_task_action_state(module.key(123),lambda old:{**old,'token':'c'*32,'status':'pending','message_id':70})
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    assert bot.send_message.await_count==1
    assert [call.args[1]['message_id'] for call in bot.request.await_args_list]==[40,50,40,50]
    assert all(call.args[0]=='deleteMessage' for call in bot.request.await_args_list)
    assert main.db.get_task_action_state(module.key(123))['status']=='pending'


@pytest.mark.parametrize('mismatch',['token','message','in_progress'])
def test_cancel_does_not_delete_stale_or_unresolved_review(system,mismatch):
    from chronos import main
    client, tasks, bot, config = system
    config.enable_learning_calendar=True
    state={'token':'b'*32,'status':'creating' if mismatch=='in_progress' else 'pending',
           'chat_id':123,'message_id':50,'expires_at':(module.datetime.now(config.tz)+timedelta(hours=2)).isoformat()}
    main.db.mutate_task_action_state(module.key(123),lambda old:state)
    bot.send_message.return_value={'ok':True,'result':{'message_id':60}}
    payload={'update_id':90601,'callback_query':{'id':'cancel','data':'replan:cancel:'+('c'*32 if mismatch=='token' else 'b'*32),
             'message':{'message_id':51 if mismatch=='message' else 50,'chat':{'id':123},'text':'Review'}}}
    assert client.post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json=payload).status_code==200
    bot.request.assert_not_awaited()
    assert main.db.get_task_action_state(module.key(123))['status']==state['status']


def test_paginated_command_preview_binds_every_page_and_cancel_removes_them(system,monkeypatch):
    from chronos import main
    client, tasks, bot, config=system
    config.enable_learning_calendar=True
    token='d'*32
    main.db.mutate_task_action_state(module.key(123),lambda old:{'token':token,'status':'pending','chat_id':123,
        'expires_at':(module.datetime.now(config.tz)+timedelta(hours=2)).isoformat()})
    monkeypatch.setattr(module,'preview',AsyncMock(return_value={'messages':[
        {'text':'First page','replan_preview_token':token},
        {'text':'Final page','replan_preview_token':token,'replan_token':token}]}))
    bot.send_message.side_effect=[{'ok':True,'result':{'message_id':40}}, {'ok':True,'result':{'message_id':50}},
                                  {'ok':True,'result':{'message_id':60}}]
    bot.request.return_value={'ok':True}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    payload={'update_id':90602,'message':{'message_id':39,'chat':{'id':123},'text':'/replan move today'}}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    saved=main.db.get_task_action_state(module.key(123))
    assert saved['preview_message_ids']==[40,50] and saved['message_id']==50
    payload={'update_id':90603,'callback_query':{'id':'cancel','data':'replan:cancel:'+token,
        'message':{'message_id':50,'chat':{'id':123},'text':'Final page'}}}
    assert client.post('/telegram/webhook',headers=headers,json=payload).status_code==200
    assert [call.args[1]['message_id'] for call in bot.request.await_args_list]==[40,50]


def test_cancel_retained_non_executable_ordered_question(system):
    from chronos import main, sequence_replan
    client,tasks,bot,config=system
    config.enable_learning_calendar=True
    state={'token':'e'*32,'status':'clarifying','ordered_sequence':True,'chat_id':123,
        'parsed':{'scope':'ISPM','ordinals':[1,2,3]},'expires_at':'2020-01-01T00:00:00+08:00'}
    main.db.mutate_task_action_state(module.key(123),lambda old:state)
    assert sequence_replan.has_clarification(state)
    bot.send_message.return_value={'ok':True,'result':{'message_id':60}}
    payload={'update_id':90604,'message':{'message_id':59,'chat':{'id':123},'text':'/cancel'}}
    assert client.post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json=payload).status_code==200
    saved=main.db.get_task_action_state(module.key(123))
    assert saved['status']=='cancelled' and not sequence_replan.has_clarification(saved)
    bot.request.assert_not_awaited()


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

def ongoing_study(setup):
    planner, calendar, events = initialize(setup)
    request_time = NOW.replace(hour=14, minute=8)
    planner.ai.parse_replan.return_value = ReplanOutput(actionable=True, start=request_time.isoformat(),
        end=NOW.replace(hour=15).isoformat(), destination_start=NOW.replace(hour=15).isoformat())
    task = planner.db.list_open_tasks()[0]
    event = events['personal']
    event['start']['dateTime'] = NOW.replace(hour=13).isoformat()
    event['end']['dateTime'] = NOW.replace(hour=14, minute=30).isoformat()
    event['extendedProperties'] = {'private': {'chronosTask': str(task['id']), 'chronosToken': 'ongoing-study'}}
    planner.update(task['id'], lambda old: {'status': 'scheduled', 'token': 'ongoing-study', 'event_id': event['id'],
        'start': event['start']['dateTime'], 'end': event['end']['dateTime'], 'minutes': 90})
    planner.db.mutate_task_action_state('learning:day:' + NOW.date().isoformat(), lambda old: {'ongoing-study': 90})
    return planner, calendar, events, task, request_time


def test_ongoing_verified_study_previews_full_duration_and_waits_for_allow(setup):
    planner, calendar, events, task, now = ongoing_study(setup)
    result = asyncio.run(module.preview(planner, 123, '清空今天現在到下午三點前的所有行程', 71, now))
    assert isinstance(result, dict), result
    from chronos.study_reflow import movable
    assert not movable(events['personal'], planner.settings, now)
    message = result['messages'][0]
    assert 'Already started' in message['text'] and 'progress is unknown' in message['text']
    module.bind(planner.db, 123, 50, message)
    state = planner.db.get_task_action_state(module.key(123))
    move = state['calendar_moves'][0]
    assert move['already_started']
    assert module.datetime.fromisoformat(move['end']) - module.datetime.fromisoformat(move['start']) == timedelta(minutes=90)
    assert module.datetime.fromisoformat(move['start']) >= now.replace(hour=15, minute=0)
    calendar.move_event.assert_not_awaited()
    assert 'updated' in asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', now))
    assert planner.get(task['id'])['minutes'] == 90
    assert planner.db.list_open_tasks()[0]['status'] == 'open'
    assert planner.db.list_open_tasks()[0]['due_at'] == task['due_at']
    asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', now))
    assert calendar.move_event.await_count == 1


@pytest.mark.parametrize('change', ['awaiting_feedback', 'token', 'state_time', 'pending_edit'])
def test_ongoing_study_still_requires_verified_live_booking(setup, change):
    planner, calendar, events, task, now = ongoing_study(setup)
    if change == 'awaiting_feedback':
        now = now.replace(hour=14, minute=31)
        planner.ai.parse_replan.return_value.start = now.isoformat()
        events['personal']['end']['dateTime'] = now.replace(hour=15).isoformat()
        planner.update(task['id'], lambda old: {**old, 'end': now.replace(hour=15).isoformat(), 'status': 'awaiting_feedback'})
    elif change == 'token':
        events['personal']['extendedProperties']['private']['chronosToken'] = 'changed'
    elif change == 'state_time':
        planner.update(task['id'], lambda old: {**old, 'start': NOW.replace(hour=12).isoformat()})
    else:
        planner.update(task['id'], lambda old: {**old, 'pending_reschedule': {'marker': 'pending'}})
    reply = asyncio.run(module.preview(planner, 123, 'free until 3pm', 72, now))
    assert 'cannot be moved automatically' in reply
    calendar.move_event.assert_not_awaited()


def test_ongoing_booking_that_ends_before_allow_is_rejected(setup):
    planner, calendar, events, task, now = ongoing_study(setup)
    result = asyncio.run(module.preview(planner, 123, 'free until 3pm', 73, now))
    module.bind(planner.db, 123, 50, result['messages'][0])
    state = planner.db.get_task_action_state(module.key(123))
    reply = asyncio.run(module.callback(planner, 123, 50, state['token'], 'allow', now.replace(minute=31)))
    assert 'Nothing changed' in reply
    calendar.move_event.assert_not_awaited()


@pytest.mark.parametrize('text,hour,minute', [
    ('清空今天現在到下午三點前的所有行程', 15, 0),
    ('清空今天現在到下午兩點半前的所有事情', 14, 30),
    ('騰出今天從現在到晚上九點之前的活動', 21, 0),
    ('清空今天現在到下午3點15分前的行程', 15, 15),
])
def test_explicit_same_day_window_is_deterministic(text, hour, minute):
    result = module.explicit_window(text, NOW)
    assert result.actionable
    assert module.datetime.fromisoformat(result.end) == NOW.replace(hour=hour, minute=minute)


def test_afternoon_window_is_deterministic():
    result = module.explicit_window('清空今天下午的行程', NOW)
    assert result.actionable
    assert module.datetime.fromisoformat(result.start) == NOW.replace(hour=13, minute=0)
    assert module.datetime.fromisoformat(result.end) == NOW.replace(hour=18, minute=0)

    during_afternoon = NOW.replace(hour=14, minute=20)
    result = module.explicit_window('清空今天下午的行程', during_afternoon)
    assert module.datetime.fromisoformat(result.start) == during_afternoon


def test_exact_screenshot_does_not_depend_on_ai(setup):
    planner, calendar, events, task, now = ongoing_study(setup)
    planner.ai.parse_replan.side_effect = AssertionError('Exact time must not call AI')
    reply = asyncio.run(module.preview(planner, 123, '清空今天現在到下午三點前的所有行程', 80, now))
    assert isinstance(reply, dict)
    planner.ai.parse_replan.assert_not_awaited()
    calendar.move_event.assert_not_awaited()


def test_past_explicit_cutoff_is_not_shifted_to_another_day(setup):
    planner, calendar, events, task, now = ongoing_study(setup)
    reply = asyncio.run(module.preview(planner, 123, '清空今天現在到下午二點前的所有行程', 81, now))
    assert 'cutoff 14:00 has passed' in reply
    planner.ai.parse_replan.assert_not_awaited()
    calendar.agenda.assert_not_awaited()


def test_explicit_window_does_not_ignore_extra_conditions():
    assert module.explicit_window('清空今天現在到下午三點前的所有行程，改排到明天', NOW) is None

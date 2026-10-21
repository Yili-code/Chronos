import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import json
import httpx

import pytest

from chronos import main
from chronos.ai import BackwardOutput, AIError
from chronos.backward_planning import (BackwardPlanner, TZ, allocate, deadline_boundary,
    explicit_deadline, key, ordered_steps, reserved_windows)
from chronos.google_calendar import CalendarError
from chronos.learning_calendar import LearningCalendar, class_windows, overlaps
from chronos.settings import Settings
from test_deadline_reminders import store
from test_system import system
from test_ai import config, mock_provider
from chronos.ai import ExternalAI

NOW = datetime(2026, 10, 19, 8, tzinfo=TZ)
CFG = Settings(_env_file=None, enable_learning_calendar=True, telegram_chat_id=123)


def output(**changes):
    return BackwardOutput.model_validate({
        'goal': 'Submit database report', 'deadline': '2026-10-30', 'question': None,
        'progress': 'Unknown', 'assumptions': ['Requirements unknown; confirm them first.'],
        'steps': [
            {'title': 'Confirm requirements', 'output': 'Verified requirements checklist', 'minutes': 30, 'depends_on': []},
            {'title': 'Write draft', 'output': 'Report draft based on verified requirements', 'minutes': 150, 'depends_on': [0]},
            {'title': 'Revise and submit', 'output': 'Reviewed report and submission receipt', 'minutes': 60, 'depends_on': [1]}], **changes})


def setup(db, model=None):
    ai, calendar = AsyncMock(), AsyncMock()
    ai.backward_plan.return_value = model or output()
    events = {}
    calendar.event.side_effect = lambda event_id: deepcopy(events.get(event_id))

    async def busy(start, end):
        return [(datetime.fromisoformat(e['start']['dateTime']), datetime.fromisoformat(e['end']['dateTime']))
                for e in events.values() if e.get('status') != 'cancelled']

    async def insert(body):
        assert body['id'] not in events
        events[body['id']] = deepcopy(body)
        return body

    calendar.busy.side_effect = busy
    calendar.insert.side_effect = insert
    return BackwardPlanner(db, calendar, ai, CFG), calendar, ai, events


def preview(planner, instruction='下週五要交資料庫報告', update_id=1):
    reply = asyncio.run(planner.preview(123, instruction, update_id, NOW))
    message = reply['messages'][-1]
    token = message['backward_plan']
    planner.bind(123, 10, message)
    return token, reply


def approve(planner, token, action='allow', now=NOW):
    return asyncio.run(planner.callback(123, 10, token, action, now))


def test_relative_dates_and_date_only_boundary():
    assert explicit_deadline('下週五要交報告', NOW) == '2026-10-30'
    assert explicit_deadline('這週五要交報告', NOW) == '2026-10-23'
    assert explicit_deadline('明天 17:00 提交', NOW) == '2026-10-20T17:00:00+08:00'
    assert deadline_boundary('2026-10-30') == datetime(2026, 10, 30, tzinfo=TZ)
    with pytest.raises(ValueError):
        deadline_boundary('2026-10-30T17:00:00')


def test_actual_ai_transport_uses_structured_output(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        data = json.loads(body['contents'][0]['parts'][0]['text'])
        assert data['timezone'] == 'Asia/Taipei'
        assert body['generationConfig']['responseJsonSchema']['properties']['steps']
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': output().model_dump_json()}]}}]})
    mock_provider(monkeypatch, handler)
    model = asyncio.run(ExternalAI(config()).backward_plan('Report', None, NOW, []))
    assert len(model.steps) == 3 and model.deadline == '2026-10-30'


def test_actual_ai_transport_handles_missing_key_and_invalid_steps(monkeypatch):
    with pytest.raises(AIError, match='not configured'):
        asyncio.run(ExternalAI(Settings(_env_file=None)).backward_plan('Report', None, NOW, []))
    def handler(request):
        body = output().model_dump()
        body['steps'][0]['minutes'] = -1
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(body)}]}}]})
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError, match='invalid response'):
        asyncio.run(ExternalAI(config()).backward_plan('Report', None, NOW, []))


def test_preview_is_read_only_then_confirmation_is_idempotent_and_linked(store):
    planner, calendar, ai, events = setup(store)
    token, reply = preview(planner)
    assert store.list_open_tasks() == [] and events == {}
    text = '\n'.join(m['text'] for m in reply['messages'])
    assert 'previous day' in text and 'AI estimated' in text and 'Requirements unknown' in text
    state = store.get_task_action_state(key(token))
    assert max(datetime.fromisoformat(s['end']) for s in state['slots']) <= deadline_boundary(state['deadline'])
    assert 'Plan created' in approve(planner, token)
    task_count, event_count = len(store.list_open_tasks()), len(events)
    assert task_count == 4 and event_count == len(state['slots'])
    assert 'Progress: 0/3' in approve(planner, token)
    assert len(store.list_open_tasks()) == task_count and len(events) == event_count
    state = store.get_task_action_state(key(token))
    child = state['children']['0']
    store.complete_task(child, NOW)
    assert 'Progress: 1/3' in planner.progress(token, 123)
    assert store.get_task(state['parent_id'])['status'] == 'open'
    assert store.get_task_action_state(f'backward:goal:{child}')['parent_id'] == state['parent_id']
    learning = LearningCalendar(store, calendar, AsyncMock(), CFG)
    assert 'backward plan' in learning.request_proposal(child)


def test_existing_goal_reused_and_existing_plan_preserved(store):
    task = store.create_task('Submit database report', None, None, NOW, {'due_date': '2026-10-30'})
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner, f"#{task['id']}")
    assert 'Plan created' in approve(planner, token)
    assert len(store.list_open_tasks()) == 4
    state = store.get_task_action_state(key(token))
    assert state['parent_id'] == task['id']
    store.complete_task(state['children']['0'], NOW)
    result = asyncio.run(planner.preview(123, f"#{task['id']} revise", 3, NOW))
    assert 'Existing work is preserved' in result and 'Progress: 1/3' in result


def test_existing_deadline_change_is_previewed_and_only_written_after_confirmation(store):
    task = store.create_task('Submit database report', None, None, NOW, {'due_date': '2026-10-28'})
    planner, _, _, _ = setup(store)
    token, reply = preview(planner, f"#{task['id']} 截止改為2026-10-30")
    assert 'also updates' in reply['messages'][0]['text']
    assert store.get_task(task['id'])['timing']['due_date'] == '2026-10-28'
    assert 'Plan created' in approve(planner, token)
    assert store.get_task(task['id'])['timing']['due_date'] == '2026-10-30'


def test_natural_language_can_select_existing_goal_without_duplicate_parent(store):
    task = store.create_task('Submit database report', None, None, NOW, {'due_date': '2026-10-30'})
    planner, _, _, _ = setup(store, output(existing_task_id=task['id']))
    token, _ = preview(planner)
    assert 'Plan created' in approve(planner, token)
    assert len(store.list_open_tasks()) == 4


def test_goal_changed_after_preview_and_learning_inflight_prevent_creation(store):
    task = store.create_task('Submit database report', None, None, NOW, {'due_date': '2026-10-30'})
    planner, _, _, events = setup(store)
    token, _ = preview(planner, f"#{task['id']}")
    store.edit_task(task['id'], 'Changed goal', None, None, {'due_date': '2026-10-30'})
    assert 'goal' in approve(planner, token)
    assert not events and len(store.list_open_tasks()) == 1
    store.edit_task(task['id'], 'Submit database report', None, None, {'due_date': '2026-10-30'})
    store.mutate_task_action_state(f"learning:task:{task['id']}", lambda old: {'status': 'creating'})
    assert 'changed' in approve(planner, token)
    assert not events


@pytest.mark.parametrize('action', ['cancel', 'edit'])
def test_cancel_and_edit_never_write_and_old_buttons_cannot_approve(store, action):
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner)
    approve(planner, token, action)
    assert 'Plan created' not in approve(planner, token)
    assert store.list_open_tasks() == [] and not events


def test_new_preview_supersedes_old_and_uses_progress_context(store):
    planner, _, ai, _ = setup(store)
    token, _ = preview(planner)
    approve(planner, token, 'edit')
    preview(planner, '初稿完成，每天最多90分鐘', 2)
    assert 'Additional user information' in ai.backward_plan.await_args.args[0]
    assert '初稿完成' in ai.backward_plan.await_args.args[0]
    assert 'no longer active' in approve(planner, token)


def test_missing_information_clarifies_without_reading_calendar(store):
    planner, calendar, _, _ = setup(store, output(deadline=None, question='When is it due?', steps=[]))
    token, reply = preview(planner, '資料庫報告')
    assert 'When is it due?' in reply['messages'][0]['text']
    calendar.busy.assert_not_awaited()
    assert store.get_task_action_state(key(token))['status'] == 'clarifying'


@pytest.mark.parametrize('failure', ['past', 'insufficient', 'cycle', 'calendar'])
def test_infeasible_or_unverified_plans_never_create(store, failure):
    model = output()
    planner, calendar, _, events = setup(store, model)
    if failure == 'past':
        model.deadline = '2026-10-18'
    elif failure == 'insufficient':
        model.deadline = '2026-10-19T09:00:00+08:00'
        calendar.busy.side_effect = None
        calendar.busy.return_value = [(NOW, NOW+timedelta(days=30))]
    elif failure == 'cycle':
        model.steps[0].depends_on = [2]
    else:
        calendar.busy.side_effect = CalendarError('offline')
    if failure in {'cycle', 'calendar'}:
        with pytest.raises((ValueError, CalendarError)):
            asyncio.run(planner.preview(123, 'Report', 1, NOW))
    else:
        reply = asyncio.run(planner.preview(123, 'Report', 1, NOW))
        if failure == 'past':
            assert 'passed' in reply
        else:
            message = reply['messages'][-1]
            assert 'short by' in message['text']
            assert not any(b['callback_data'].startswith('backward:allow') for b in message['reply_markup']['inline_keyboard'][0])
    assert not events and not store.list_open_tasks()


def test_confirm_rechecks_calendar_and_task_snapshot(store):
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner)
    calendar.busy.side_effect = None
    calendar.busy.return_value = [(NOW, NOW+timedelta(days=30))]
    result = approve(planner, token)
    assert 'could not verify' in result
    assert not events and not store.list_open_tasks()


def test_partial_insert_and_uncertain_response_recover_without_duplicates(store):
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner)
    insert = calendar.insert.side_effect
    calls = 0

    async def uncertain(body):
        nonlocal calls
        calls += 1
        event = await insert(body)
        if calls == 2:
            raise CalendarError('lost response')
        return event

    calendar.insert.side_effect = uncertain
    result = approve(planner, token)
    assert '3/3 step tasks' in result and '1/' in result
    assert len(events) == 2
    assert store.get_task_action_state(key(token))['status'] == 'partial'
    assert 'recovered first' in approve(planner, token, 'cancel')
    assert 'Plan created' in approve(planner, token)
    assert len(events) == len(store.get_task_action_state(key(token))['slots'])
    assert len(store.list_open_tasks()) == 4


def test_partial_database_failure_resumes_linked_tasks(store, monkeypatch):
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner)
    create = store.create_task
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError('database unavailable')
        return create(*args, **kwargs)

    monkeypatch.setattr(store, 'create_task', fail_once)
    assert '1/3 step tasks' in approve(planner, token)
    assert len(store.list_open_tasks()) == 2 and not events
    assert 'Plan created' in approve(planner, token)
    assert len(store.list_open_tasks()) == 4


def test_reservations_prevent_a_second_plan_from_reusing_slots(store):
    planner, _, _, _ = setup(store)
    token, _ = preview(planner)
    assert 'Plan created' in approve(planner, token)
    original = reserved_windows(store)
    planner.ai.backward_plan.return_value = output(goal='Another report')
    other, _ = preview(planner, 'Another report', 2)
    for slot in store.get_task_action_state(key(other))['slots']:
        assert not overlaps(datetime.fromisoformat(slot['start']), datetime.fromisoformat(slot['end']), original)


def test_recovery_does_not_replace_an_owner_modified_event(store):
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner)
    insert = calendar.insert.side_effect

    async def uncertain(body):
        await insert(body)
        raise CalendarError('unknown response')

    calendar.insert.side_effect = uncertain
    approve(planner, token)
    next(iter(events.values()))['status'] = 'cancelled'
    calls = calendar.insert.await_count
    assert 'could not verify' in approve(planner, token)
    assert calendar.insert.await_count == calls


def test_dependency_order_working_hours_classes_and_daily_capacity():
    steps = [s.model_dump() for s in output().steps]
    policy = None
    slots, buffer, missing = allocate(steps, NOW, deadline_boundary('2026-10-30'), [], CFG, policy, {}, 540)
    assert missing == 0 and buffer >= 48
    for i in range(1, len(slots)):
        assert slots[i-1]['end'] <= slots[i]['start']
    for i in range(1, len(steps)):
        previous = max(s['end'] for s in slots if s['step'] == i-1)
        current = min(s['start'] for s in slots if s['step'] == i)
        assert previous <= current
    for slot in slots:
        start, end = (datetime.fromisoformat(slot[k]) for k in ('start', 'end'))
        assert 9 <= start.hour and end.hour <= 21
        assert not overlaps(start, end, class_windows(start.date(), TZ))
    for day in {s['start'][:10] for s in slots}:
        minutes = sum((datetime.fromisoformat(s['end'])-datetime.fromisoformat(s['start'])).total_seconds()/60 for s in slots if s['start'][:10] == day)
        minutes += sum((b-a).total_seconds()/60 for a,b in class_windows(datetime.fromisoformat(day).date(), TZ))
        assert minutes <= 540
    with pytest.raises(ValueError):
        ordered_steps([{'depends_on': [1]}, {'depends_on': [0]}])


def test_changed_budget_wrong_card_expiry_and_concurrent_confirmation(store):
    planner, calendar, _, events = setup(store)
    token, _ = preview(planner)
    assert 'not bound' in asyncio.run(planner.callback(123, 99, token, 'allow', NOW))
    assert 'expired' in approve(planner, token, now=NOW+timedelta(hours=3))
    store.mutate_task_action_state('backward:writer', lambda old: {'claim': 'other', 'until': (NOW+timedelta(minutes=1)).isoformat()})
    assert 'running' in approve(planner, token)
    store.mutate_task_action_state('backward:writer', lambda old: {})
    for slot in store.get_task_action_state(key(token))['slots']:
        store.mutate_task_action_state('learning:day:'+slot['start'][:10], lambda old: {'other': 540})
    assert 'budget' in approve(planner, token)
    assert not events and not store.list_open_tasks()


def test_changed_work_hours_at_confirmation_do_not_write(store):
    planner, _, _, events = setup(store)
    token, _ = preview(planner)
    old = planner.settings
    planner.settings = old.model_copy(update={'learning_end_hour': 10})
    assert 'could not verify' in approve(planner, token)
    assert not events and not store.list_open_tasks()


def test_webhook_command_confirmation_and_redelivery(system, monkeypatch):
    client, tasks, bot, cfg = system
    cfg.enable_learning_calendar = True
    now = datetime.now(TZ)
    model = output(deadline=(now+timedelta(days=7)).date().isoformat())
    planner, cal, ai, events = setup(main.db, model)
    monkeypatch.setattr(main, 'google_calendar', cal)
    monkeypatch.setattr(main.ai, 'backward_plan', ai.backward_plan)
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 51}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 91001, 'message': {'message_id': 50, 'chat': {'id': 123}, 'text': '/backward Report'}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert ai.backward_plan.await_count == 1 and tasks.list_open() == []
    token = planner.session(123)['token']
    payload = {'update_id': 91002, 'callback_query': {'id': 'confirm', 'data': 'backward:allow:'+token,
        'message': {'message_id': 51, 'chat': {'id': 123}}}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert len(tasks.list_open()) == 4 and events
    main.ai.parse.assert_not_awaited()


def test_webhook_edited_command_does_not_replay_planning(system, monkeypatch):
    client, tasks, bot, cfg = system
    model = AsyncMock()
    monkeypatch.setattr(main.ai, 'backward_plan', model)
    response = client.post('/telegram/webhook', headers={'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}, json={
        'update_id': 91003, 'edited_message': {'message_id': 50, 'chat': {'id': 123}, 'text': '/backward Report'}})
    assert response.status_code == 200
    model.assert_not_awaited()
    assert tasks.list_open() == []


@pytest.mark.parametrize('text', ['/backward', '/backward@ChronosBot', '/backward progress', '/backward progress #999', '/command Report'])
def test_webhook_usage_progress_and_retired_placeholder_never_plan(system, monkeypatch, text):
    client, tasks, bot, cfg = system
    model = AsyncMock()
    monkeypatch.setattr(main.ai, 'backward_plan', model)
    response = client.post('/telegram/webhook', headers={'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}, json={
        'update_id': 91005, 'message': {'message_id': 55, 'chat': {'id': 123}, 'text': text}})
    assert response.status_code == 200
    model.assert_not_awaited()
    main.ai.parse.assert_not_awaited()
    assert tasks.list_open() == []
    reply = bot.send_message.await_args.args[1]
    if text in {'/backward', '/backward@ChronosBot'}:
        assert reply == 'Use /info backward for planning instructions.'
    elif text.startswith('/command'):
        assert 'Unknown command' in reply
    else:
        assert 'No matching plan' in reply


def test_webhook_fixed_id_starts_planning_and_progress_does_not_call_ai(system, monkeypatch):
    client, tasks, bot, cfg = system
    cfg.enable_learning_calendar = True
    now = datetime.now(TZ)
    task = tasks.create('Submit database report', timing={'due_date': (now+timedelta(days=10)).date().isoformat()})
    planner, cal, ai, events = setup(main.db, output(deadline=(now+timedelta(days=10)).date().isoformat()))
    monkeypatch.setattr(main, 'google_calendar', cal)
    monkeypatch.setattr(main.ai, 'backward_plan', ai.backward_plan)
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 56}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 91006, 'message': {'message_id': 55, 'chat': {'id': 123}, 'text': f"/backward #{task['id']}"}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert ai.backward_plan.await_count == 1
    token = planner.session(123)['token']
    state = main.db.get_task_action_state(key(token))
    assert state['parent_id'] == task['id'] and len(tasks.list_open()) == 1
    main.db.mutate_task_action_state(f"backward:goal:{task['id']}", lambda old: {'token': token})
    payload = {'update_id': 91007, 'message': {'message_id': 57, 'chat': {'id': 123}, 'text': f"/backward progress #{task['id']}"}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert 'Progress: 0/3' in bot.send_message.await_args.args[1]
    assert ai.backward_plan.await_count == 1 and not events


def test_long_preview_delivery_resumes_without_repeating_sent_parts(system):
    client, tasks, bot, cfg = system
    planner, _, _, _ = setup(main.db)
    state = {'token': 'a'*32, 'chat_id': 123, 'goal': 'Large report', 'status': 'draft',
        'deadline': '2026-11-30', 'instruction': 'Large report', 'progress': 'Unknown', 'daily_cap': 540,
        'assumptions': ['Estimates'], 'steps': [s.model_dump() for s in output().steps],
        'buffer': 60, 'missing': 0, 'slots': [
            {'step': i % 3, 'start': '2026-10-20T09:00:00+08:00', 'end': '2026-10-20T10:00:00+08:00'} for i in range(180)]}
    main.db.mutate_task_action_state(key(state['token']), lambda old: state)
    reply = planner.render(state)
    assert len(reply['messages']) > 1
    assert all(len(m['text']) <= 3900 for m in reply['messages'])
    assert 'reply_markup' not in reply['messages'][0]
    receipt = main.db.process_update(91004, lambda: reply)
    bot.send_message.side_effect = [
        {'ok': True, 'result': {'message_id': 61}}, {'ok': False}]
    with pytest.raises(main.HTTPException):
        asyncio.run(main.deliver_learning_reply(123, 91004, receipt))
    assert main.db.get_update(91004)['delivered_count'] == 1
    bot.send_message.side_effect = None
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 62}}
    asyncio.run(main.deliver_learning_reply(123, 91004, main.db.get_update(91004)))
    assert bot.send_message.await_count == len(reply['messages'])+1
    assert main.db.get_task_action_state(key(state['token']))['message_id'] == 62

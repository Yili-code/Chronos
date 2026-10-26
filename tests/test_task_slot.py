import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import task_slot, calendar_replan, main
from chronos.ai import DurationOutput, ScheduleEditOutput
from chronos.google_calendar import CalendarError
from chronos.task_actions import task_fields
from test_learning_calendar import setup, NOW
from test_intents import store, classify, run


def initialize(setup, *, booked=False, conflict=True):
    planner, _, calendar, _ = setup
    task = planner.db.create_task('Graph theory algorithm cheat sheet', None, 'graphs', NOW, {'estimated_minutes': 60})
    start = (NOW + timedelta(days=6)).replace(hour=15, minute=30)
    planner.ai = AsyncMock()
    events = {}
    def event(event_id, title, a, b, task_id=None, token=None):
        return {'id': event_id, 'etag': 'v1', 'summary': title, '_calendar_id': 'primary',
            'creator': {'email': 'owner@example.com'}, 'organizer': {'self': True},
            'start': {'dateTime': a.isoformat()}, 'end': {'dateTime': b.isoformat()},
            'extendedProperties': {'private': {'chronosTask': str(task_id), 'chronosToken': token}} if task_id else {}}
    if conflict:
        events['conflict'] = event('conflict', 'Personal reading', start, start + timedelta(minutes=45))
    if booked:
        a = start.replace(hour=9, minute=0)
        events['target'] = event('target', task['title'], a, a + timedelta(hours=1), task['id'], 'original')
        planner.update(task['id'], lambda old: {'status': 'scheduled', 'token': 'original',
            'start': a.isoformat(), 'end': (a + timedelta(hours=1)).isoformat(), 'minutes': 60,
            'event_id': 'target', 'snapshot': task_fields(task)})
        planner.db.mutate_task_action_state('learning:day:' + start.date().isoformat(), lambda old: {'original': 60})
    calendar.agenda.side_effect = lambda *args: list(deepcopy(events).values())
    calendar.event.side_effect = lambda event_id: deepcopy(events.get(event_id))
    async def move(event_id, body, etag):
        assert events[event_id]['etag'] == etag
        events[event_id].update(body)
        events[event_id]['etag'] = 'v2'
        return deepcopy(events[event_id])
    async def insert(body):
        assert body['id'] not in events
        events[body['id']] = {**deepcopy(body), 'etag': 'v1'}
        return deepcopy(events[body['id']])
    calendar.move_event.side_effect = move
    calendar.insert.side_effect = insert
    return planner, task, calendar, events, start


def preview(planner, task, start, update=1):
    result = asyncio.run(task_slot.preview(planner, 123, task, 'Prepare cheat sheet at 15:30', update, NOW, start=start))
    if isinstance(result, dict):
        calendar_replan.bind(planner.db, 123, 50, result['messages'][0])
    return result


def approve(planner, now=NOW, message_id=50):
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    return asyncio.run(calendar_replan.callback(planner, 123, message_id, state['token'], 'allow', now))


@pytest.mark.parametrize('booked', [False, True])
def test_reuses_existing_task_and_exact_start_while_moving_conflicts(setup, booked):
    planner, task, calendar, events, start = initialize(setup, booked=booked)
    before = deepcopy(planner.db.list_open_tasks())
    result = preview(planner, task, start)
    assert isinstance(result, dict), result
    calendar.move_event.assert_not_awaited()
    calendar.insert.assert_not_awaited()
    reply = approve(planner)
    assert 'Scheduled existing task' in reply
    assert planner.db.list_open_tasks() == before
    saved = planner.get(task['id'])
    assert saved['start'] == start.isoformat() and saved['minutes'] == 60
    assert events['conflict']['start']['dateTime'] >= saved['end']
    assert calendar.insert.await_count == (0 if booked else 1)
    if booked:
        assert saved['event_id'] == 'target' and saved['token'] == 'original'
    assert 'already' in approve(planner)
    assert calendar.insert.await_count == (0 if booked else 1)


@pytest.mark.parametrize('fixed', ['shared', 'recurring', 'foreign', 'all_day'])
def test_fixed_conflicts_prevent_all_writes(setup, fixed):
    planner, task, calendar, events, start = initialize(setup)
    item = events['conflict']
    if fixed == 'shared': item['attendees'] = [{'email': 'friend@example.com'}]
    elif fixed == 'recurring': item['recurringEventId'] = 'series'
    elif fixed == 'foreign': item['_calendar_id'] = 'other'
    else:
        item['start'] = {'date': start.date().isoformat()}
        item['end'] = {'date': (start + timedelta(days=1)).date().isoformat()}
    reply = preview(planner, task, start)
    assert '不能自動移動' in reply
    calendar.move_event.assert_not_awaited()
    calendar.insert.assert_not_awaited()
    assert planner.get(task['id']) is None


def test_new_conflict_before_allow_causes_no_writes(setup):
    planner, task, calendar, events, start = initialize(setup, conflict=False)
    preview(planner, task, start)
    events['new'] = {'id': 'new', '_calendar_id': 'other', 'etag': 'n1', 'summary': 'Meeting',
        'start': {'dateTime': start.isoformat()}, 'end': {'dateTime': (start + timedelta(hours=1)).isoformat()}}
    reply = approve(planner)
    assert isinstance(reply, dict) and 'paused' in reply['messages'][0]['text']
    calendar.move_event.assert_not_awaited()
    calendar.insert.assert_not_awaited()


def test_changed_task_and_wrong_message_cannot_apply(setup):
    planner, task, calendar, _, start = initialize(setup)
    preview(planner, task, start)
    assert 'no longer active' in approve(planner, message_id=999)
    planner.db.complete_task(task['id'], NOW)
    assert 'paused' in approve(planner)['messages'][0]['text']
    calendar.move_event.assert_not_awaited()
    calendar.insert.assert_not_awaited()


def test_lost_move_response_recovers_without_repeating_move(setup):
    planner, task, calendar, events, start = initialize(setup)
    preview(planner, task, start)
    move = calendar.move_event.side_effect
    async def lost(*args):
        await move(*args)
        raise CalendarError('Lost response')
    calendar.move_event.side_effect = lost
    assert 'paused' in approve(planner)['messages'][0]['text']
    calendar.move_event.side_effect = move
    assert 'Scheduled existing task' in approve(planner)
    assert calendar.move_event.await_count == 1
    assert calendar.insert.await_count == 1


def test_lost_insert_response_recovers_deterministic_event(setup):
    planner, task, calendar, events, start = initialize(setup, conflict=False)
    preview(planner, task, start)
    insert = calendar.insert.side_effect
    async def lost(body):
        await insert(body)
        raise CalendarError('Lost response')
    calendar.insert.side_effect = lost
    assert 'paused' in approve(planner)['messages'][0]['text']
    assert 'Scheduled existing task' in approve(planner)
    assert calendar.insert.await_count == 1
    assert len(events) == 1


def test_standing_authorization_executes_exact_slot(setup):
    planner, task, calendar, _, start = initialize(setup)
    planner.settings = planner.settings.model_copy(update={'learning_auto_accept': True})
    assert 'Scheduled existing task' in preview(planner, task, start)
    assert planner.get(task['id'])['start'] == start.isoformat()
    assert calendar.insert.await_count == 1


@pytest.mark.parametrize('reason', ['past', 'meal', 'reservation', 'deadline'])
def test_infeasible_exact_slot_never_falls_back_to_another_date(setup, reason):
    planner, task, calendar, _, start = initialize(setup, conflict=False)
    if reason == 'past': start = NOW - timedelta(minutes=1)
    elif reason == 'meal': start = start.replace(hour=12, minute=0)
    elif reason == 'reservation':
        other = planner.db.create_task('Other task', None, None, NOW, {})
        planner.update(other['id'], lambda old: {'status': 'creating', 'start': start.isoformat(),
            'end': (start + timedelta(hours=1)).isoformat()})
    else:
        from chronos.tasks import TaskService
        TaskService(planner.db, planner.settings.tz).edit(task['id'], task['title'], NOW + timedelta(days=1), task['project'], task['timing'])
        task = planner.db.get_task(task['id'])
    result = preview(planner, task, start)
    assert isinstance(result, str), result
    assert planner.get(task['id']) is None
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('action', ['task_schedule', 'plan_request', 'task_create'])
def test_existing_timed_intent_never_reaches_task_creation(store, monkeypatch, action):
    task = main.tasks.create('Graph theory algorithm cheat sheet')
    classify(monkeypatch, action=action, task_ids=[task['id']], start=NOW + timedelta(days=1))
    monkeypatch.setattr(main, 'settings', main.settings.model_copy(update={'enable_learning_calendar': True}))
    handler = AsyncMock(return_value='Scheduled existing task')
    monkeypatch.setattr(task_slot, 'preview', handler)
    result = run('三點半開始我要準備圖論演算法的大抄')
    assert result['reply'] == 'Scheduled existing task'
    assert len(main.tasks.list_open()) == 1
    main.ai.parse.assert_not_awaited()
    assert handler.await_args.args[2]['id'] == task['id']


def test_ambiguous_existing_match_requires_selection(store, monkeypatch):
    a = main.tasks.create('Graph theory cheat sheet A')
    b = main.tasks.create('Graph theory cheat sheet B')
    classify(monkeypatch, action='task_schedule', task_ids=[a['id'], b['id']],
        target_selection='ambiguous', start=NOW + timedelta(days=1))
    reply = run('三點半開始準備圖論大抄')['reply']
    assert '哪一項' in reply
    assert len(main.tasks.list_open()) == 2
    main.ai.parse.assert_not_awaited()


def test_exact_start_keeps_non_quarter_hour_and_short_lead_time(setup):
    planner, task, calendar, _, start = initialize(setup, conflict=False)
    start = start.replace(minute=32)
    now = start - timedelta(minutes=5)
    result = asyncio.run(task_slot.preview(planner, 123, task, 'Start in five minutes', 1, now, start=start))
    calendar_replan.bind(planner.db, 123, 50, result['messages'][0])
    assert 'Scheduled existing task' in approve(planner, now=now)
    assert planner.get(task['id'])['start'] == start.isoformat()


def test_busy_booking_remains_exact_and_requires_explicit_recovery(setup):
    planner, task, calendar, _, start = initialize(setup, conflict=False)
    preview(planner, task, start)
    calendar.busy.return_value = [(start, start + timedelta(hours=1))]
    result = approve(planner)
    assert 'paused' in result['messages'][0]['text']
    assert planner.get(task['id'])['status'] == 'needs_replan'
    assert planner.get(task['id'])['start'] == start.isoformat()
    calendar.insert.assert_not_awaited()
    calendar.busy.return_value = []
    assert 'Scheduled existing task' in approve(planner)
    assert planner.get(task['id'])['start'] == start.isoformat()


def test_concurrent_allow_has_one_writer(setup):
    planner, task, calendar, _, start = initialize(setup)
    preview(planner, task, start)
    move = calendar.move_event.side_effect
    async def slow(*args):
        await asyncio.sleep(0)
        return await move(*args)
    calendar.move_event.side_effect = slow
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    async def both():
        return await asyncio.gather(*(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW) for _ in range(2)))
    replies = asyncio.run(both())
    assert any('already being applied' in r for r in replies)
    assert any('Scheduled existing task' in r for r in replies)
    assert calendar.move_event.await_count == calendar.insert.await_count == 1


def test_parser_and_duration_estimator_keep_existing_identity(setup):
    planner, task, calendar, _, start = initialize(setup, conflict=False)
    from chronos.tasks import TaskService
    TaskService(planner.db, planner.settings.tz).edit(task['id'], task['title'], None, task['project'], {})
    task = planner.db.get_task(task['id'])
    planner.ai.parse_schedule_edit.return_value = ScheduleEditOutput(actionable=True, timing='exact',
        start=start.isoformat(), minutes=None, window_end=None, progress_note='')
    planner.ai.estimate_duration.return_value = DurationOutput(estimated_minutes=60, confidence='medium', reason='Synthetic estimate')
    result = asyncio.run(task_slot.preview(planner, 123, task, 'Start this task at 15:30', 1, NOW))
    calendar_replan.bind(planner.db, 123, 50, result['messages'][0])
    assert 'Scheduled existing task' in approve(planner)
    assert planner.get(task['id'])['minutes'] == 60
    assert len(planner.db.list_open_tasks()) == 2


def test_completed_target_during_lost_booking_response_cannot_be_replayed(setup):
    planner, task, calendar, _, start = initialize(setup, conflict=False)
    preview(planner, task, start)
    calendar.insert.side_effect = CalendarError('No response')
    assert 'paused' in approve(planner)['messages'][0]['text']
    planner.db.complete_task(task['id'], NOW)
    assert 'target task changed' in approve(planner)['messages'][0]['text']
    assert calendar.insert.await_count == 1


def test_existing_target_without_time_does_not_create_duplicate(store, monkeypatch):
    task = main.tasks.create('Graph theory algorithm cheat sheet')
    classify(monkeypatch, action='task_create', task_ids=[task['id']])
    assert '已存在' in run('我要準備圖論的大抄')['reply']
    assert len(main.tasks.list_open()) == 1
    main.ai.parse.assert_not_awaited()


def test_agenda_reply_reports_success_after_its_own_schedule_change(store, monkeypatch):
    from chronos.intents import prepare
    task = main.tasks.create('Graph theory algorithm cheat sheet')
    plan = {'status': 'scheduled', 'token': 'old', 'event_id': 'event', 'start': NOW.isoformat(),
            'end': (NOW + timedelta(hours=1)).isoformat()}
    store.mutate_task_action_state(f"learning:task:{task['id']}", lambda old: plan)
    context = {'entries': [{'task_id': task['id'], 'snapshot': task_fields(task), 'plan': plan}]}
    classify(monkeypatch, action='task_schedule', task_ids=[task['id']], start=NOW + timedelta(days=1))
    monkeypatch.setattr(main, 'settings', main.settings.model_copy(update={'enable_learning_calendar': True}))
    async def changed(*args, **kwargs):
        store.mutate_task_action_state(f"learning:task:{task['id']}", lambda old: {**old, 'start': (NOW + timedelta(days=1)).isoformat()})
        return 'Scheduled existing task'
    monkeypatch.setattr(task_slot, 'preview', changed)
    action = asyncio.run(prepare(main, '明天排這項任務', update_id=1, chat_id=123, agenda_context=context))
    assert store.process_update(1, action)['reply'] == 'Scheduled existing task'


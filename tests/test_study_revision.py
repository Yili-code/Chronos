import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import study_revision as revision, calendar_replan
from chronos.ai import StudyRevisionOutput, StudyRevisionItem
from chronos.task_actions import task_fields
from test_learning_calendar import setup, NOW
from test_intents import store, classify, run
from chronos import main


def initialize(setup, *, booked=True, minimum=120):
    planner, first, calendar, _ = setup
    planner.ai = AsyncMock()
    day = (NOW + timedelta(days=6)).date()  # Sunday, no course timetable.
    start = NOW.replace(day=day.day, hour=9)
    second = planner.db.create_task('Study ISPM Chapter 2', None, 'ISPM', NOW, {})
    other = planner.db.create_task('Review database chapter 2', None, 'database', NOW, {})
    # The fixture task has a short deadline; use separate chapter tasks for Sunday.
    first = planner.db.create_task('Study ISPM Chapter 1', None, 'ISPM', NOW, {})
    events = {}
    if booked:
        for index, (task, minutes) in enumerate(((first, 60), (other, 90))):
            a = start + timedelta(hours=index * 4)
            b = a + timedelta(minutes=minutes)
            token = f'booking{index}'
            event_id = f'event{index}'
            planner.update(task['id'], lambda old, a=a, b=b, token=token, event_id=event_id, minutes=minutes, task=task: {
                'status': 'scheduled', 'token': token, 'event_id': event_id, 'minutes': minutes,
                'snapshot': task_fields(task), 'start': a.isoformat(), 'end': b.isoformat()})
            events[event_id] = {'id': event_id, 'etag': 'v1', 'summary': task['title'], '_calendar_id': 'primary',
                'creator': {'email': 'owner@example.com'}, 'organizer': {'self': True},
                'start': {'dateTime': a.isoformat()}, 'end': {'dateTime': b.isoformat()},
                'extendedProperties': {'private': {'chronosTask': str(task['id']), 'chronosToken': token}}}
        planner.db.mutate_task_action_state('learning:day:' + day.isoformat(), lambda old: {'booking0': 60, 'booking1': 90})
    planner.ai.parse_study_revision.return_value = StudyRevisionOutput(actionable=True, day=day.isoformat(),
        items=[StudyRevisionItem(task_id=t['id'], minimum_minutes=minimum) for t in (first, second)])
    calendar.agenda.side_effect = lambda *args: list(deepcopy(events).values())
    calendar.event.side_effect = lambda event_id: deepcopy(events.get(event_id))
    async def move(event_id, body, etag):
        assert events[event_id]['etag'] == etag
        events[event_id].update(body)
        events[event_id]['etag'] = 'v2'
        return deepcopy(events[event_id])
    calendar.move_event.side_effect = move
    return planner, calendar, events, first, second, other


def preview(planner, update=1):
    result = asyncio.run(revision.preview(planner, 123, '2026-10-25 ISPM need at least 120 min per chapter and i did not finish ch1, ch2. replan', update, NOW))
    if isinstance(result, dict):
        calendar_replan.bind(planner.db, 123, 50, result['messages'][0])
    return result


def test_revision_keeps_other_subject_duration_and_queues_unfinished_once(setup):
    planner, calendar, events, first, second, other = initialize(setup)
    result = preview(planner)
    assert isinstance(result, dict), result
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    assert {m['task_id']: m['minutes'] for m in state['calendar_moves']} == {first['id']: 120, other['id']: 90}
    assert state['revision_additions'][0]['task_id'] == second['id']
    calendar.move_event.assert_not_awaited()
    calendar.insert.assert_not_awaited()
    reply = asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert 'Calendar updated' in reply
    assert planner.get(first['id'])['minutes'] == 120
    assert planner.get(other['id'])['minutes'] == 90
    assert planner.get(second['id'])['requested_minutes'] == 120
    assert planner.get(second['id'])['status'] == 'queued'
    assert planner.db.get_task(second['id'])['status'] == 'open'
    asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert calendar.move_event.await_count == 2
    assert sum(planner.db.get_task_action_state('learning:day:' + state['start'][:10]).values()) == 210
    calendar.insert.assert_not_awaited()


def test_capacity_failure_keeps_entire_original_plan(setup):
    planner, calendar, _, first, second, _ = initialize(setup)
    planner.settings.learning_daily_minutes = 180
    before = planner.get(first['id'])
    result = preview(planner)
    assert 'Nothing changed' in result and 'daily limit' in result
    assert planner.get(first['id']) == before and planner.get(second['id']) is None
    calendar.move_event.assert_not_awaited()


@pytest.mark.parametrize('change', ['task', 'calendar', 'limit', 'pending'])
def test_changed_constraints_prevent_writes(setup, change):
    planner, calendar, events, first, second, _ = initialize(setup)
    assert isinstance(preview(planner), dict)
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    if change == 'task':
        planner.update(second['id'], lambda old: {'status': 'queued', 'token': 'new'})
    elif change == 'calendar':
        events['event0']['etag'] = 'external'
    elif change == 'limit':
        planner.settings.learning_daily_minutes = 100
    else:
        task = planner.db.create_task('New pending work', None, None, NOW, {})
        item = state['calendar_moves'][0]
        planner.update(task['id'], lambda old: {'status': 'creating', 'start': item['start'], 'end': item['end']})
    reply = asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert 'Nothing changed' in reply
    calendar.move_event.assert_not_awaited()
    assert planner.get(second['id']) is None or planner.get(second['id'])['token'] == 'new'


def test_all_new_sessions_use_review_without_calendar_moves(setup):
    planner, calendar, _, _, second, _ = initialize(setup, booked=False)
    assert isinstance(preview(planner), dict)
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    reply = asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert 'Study plan reviewed' in reply
    assert planner.get(second['id'])['status'] == 'queued'
    calendar.move_event.assert_not_awaited()


def test_unknown_date_or_missing_chapter_never_mutates(setup):
    planner, calendar, _, first, _, _ = initialize(setup)
    planner.ai.parse_study_revision.return_value = StudyRevisionOutput(actionable=False, question='Which chapter?')
    before = planner.get(first['id'])
    assert preview(planner) == 'Which chapter?'
    assert planner.get(first['id']) == before
    calendar.agenda.assert_not_awaited()


def test_replan_classifier_fallback_uses_study_revision(store, monkeypatch):
    monkeypatch.setattr(main.settings, 'enable_learning_calendar', True)
    classify(monkeypatch, action='replan')
    mock = AsyncMock(return_value='Study revision review')
    monkeypatch.setattr(revision, 'preview', mock)
    wrong = AsyncMock(side_effect=AssertionError('Free-window route must not run'))
    monkeypatch.setattr(calendar_replan, 'preview', wrong)
    result = run('2026-10-25 ISPM need at least 120 min per chapter and i did not finish ch1, ch2. replan')
    assert result['reply'] == 'Study revision review'
    mock.assert_awaited_once()
    wrong.assert_not_awaited()


def test_recent_context_requires_successful_delivery(setup):
    planner, _, _, _ = setup
    key = 'evening:agenda:' + (NOW.date() + timedelta(days=1)).isoformat()
    planner.db.mutate_task_action_state(key, lambda old: {'status': 'uncertain', 'parts': ['Tomorrow agenda']})
    assert revision.recent_context(planner.db, NOW) is None
    planner.db.mutate_task_action_state(key, lambda old: {**old, 'status': 'sent'})
    assert revision.recent_context(planner.db, NOW)['day'] == (NOW.date() + timedelta(days=1)).isoformat()


def test_parser_uses_validated_provider_schema(monkeypatch):
    import json
    import httpx
    from chronos.ai import ExternalAI
    from chronos.settings import Settings
    cfg = Settings(_env_file=None, gemini_api_key='test-key', gemini_model='test-model')
    real = httpx.AsyncClient
    def respond(request):
        body = json.loads(request.content)
        assert body['generationConfig']['responseJsonSchema']['properties']['items']['type'] == 'array'
        data = json.loads(body['contents'][0]['parts'][0]['text'])
        assert data['recent_agenda']['day'] == '2026-10-25'
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps({
            'actionable': True, 'day': '2026-10-25', 'items': [{'task_id': 42, 'minimum_minutes': 120}]})}]}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(respond), **kw))
    result = asyncio.run(ExternalAI(cfg).parse_study_revision('ISPM chapter 1 at least 120 min',
        [{'id': 42, 'title': 'ISPM Chapter 1'}], {'day': '2026-10-25'}, NOW))
    assert result.items[0].minimum_minutes == 120


def test_parser_cannot_shorten_explicit_minimum(setup):
    planner, calendar, _, _, _, _ = initialize(setup, minimum=60)
    assert 'below your stated minimum' in preview(planner)
    calendar.agenda.assert_not_awaited()


def test_existing_longer_session_is_preserved(setup):
    planner, _, events, first, _, _ = initialize(setup)
    event = events['event0']
    event['end']['dateTime'] = (NOW.replace(day=25, hour=9) + timedelta(minutes=150)).isoformat()
    planner.update(first['id'], lambda old: {**old, 'end': event['end']['dateTime'], 'minutes': 150})
    planner.db.mutate_task_action_state('learning:day:2026-10-25', lambda old: {**old, 'booking0': 150})
    result = preview(planner)
    assert isinstance(result, dict), result
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    assert next(m for m in state['calendar_moves'] if m['task_id'] == first['id'])['minutes'] == 150


@pytest.mark.parametrize('action', ['cancel', 'expired'])
def test_unapproved_reviews_keep_original_bookings(setup, action):
    planner, calendar, _, first, second, _ = initialize(setup)
    assert isinstance(preview(planner), dict)
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    before = planner.get(first['id'])
    reply = asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'],
        'cancel' if action == 'cancel' else 'allow', NOW if action == 'cancel' else NOW + timedelta(hours=3)))
    assert 'Cancelled' in reply or 'expired' in reply
    assert planner.get(first['id']) == before and planner.get(second['id']) is None
    calendar.move_event.assert_not_awaited()


def test_uncertain_move_recovers_without_repeating_successful_patch(setup):
    from chronos.google_calendar import CalendarError
    planner, calendar, _, first, second, _ = initialize(setup)
    assert isinstance(preview(planner), dict)
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    move = calendar.move_event.side_effect
    failed = False
    async def uncertain(event_id, body, etag):
        nonlocal failed
        result = await move(event_id, body, etag)
        if not failed:
            failed = True
            raise CalendarError('Response lost after successful move')
        return result
    calendar.move_event.side_effect = uncertain
    reply = asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert 'paused' in reply
    assert planner.get(second['id']) is None
    reply = asyncio.run(calendar_replan.callback(planner, 123, 50, state['token'], 'allow', NOW))
    assert 'Calendar updated' in reply
    assert calendar.move_event.await_count == 2
    assert planner.get(first['id'])['minutes'] == 120
    assert planner.get(second['id'])['requested_minutes'] == 120


def test_deadline_failure_keeps_plan(setup):
    planner, calendar, _, _, second, _ = initialize(setup)
    planner.db.edit_task(second['id'], second['title'], NOW + timedelta(days=1), second['project'], {})
    result = preview(planner)
    assert 'deadline' in result and 'Nothing changed' in result
    calendar.move_event.assert_not_awaited()


def test_model_omission_does_not_drop_named_chapter(setup):
    planner, _, _, first, second, _ = initialize(setup)
    # Use Chapter 2 in the request; Chapter 1 is absent and must be called out.
    missing = planner.db.create_task('Study ISPM Chapter 3', None, 'ISPM', NOW, {})
    result = preview(planner, update=2)
    assert isinstance(result, dict), result
    state = planner.db.get_task_action_state(calendar_replan.key(123))
    assert {item['task_id'] for item in state['revision_additions']} == {second['id'], missing['id']}

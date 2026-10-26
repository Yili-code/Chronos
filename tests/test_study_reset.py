import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from chronos import study_reset as reset, main
from chronos.ai import AIError
from chronos.google_calendar import CalendarError, GoogleCalendar
from chronos.task_actions import task_fields
from chronos.intents import Intent
from chronos.tasks import TaskService
from test_learning_calendar import setup, NOW
from test_intents import store, run, classify

INPUTS = [
    '移除所有 study 行程，按照各單位的先後順序重新安排',
    '把全部讀書行程清掉重排，章節要照順序',
    '所有學習時段重新排，ISPM ch1 要在 ch2 前面',
    '幫我重建整份讀書計畫，先備知識要先讀',
    '現在的 study 安排全部重來，先讀前面的章節',
    '重新安排所有讀書任務，GA 考古準備要排在期中考前',
    '把剩下的讀書行程全部打掉重排，照課程單元順序',
    '讀書安排順序亂了，全部重新整理，別讓後面的章節先開始',
    'Reset all study sessions and reschedule them in prerequisite order',
    'study 全部重排，ISPM ch1 → ch2 → ch3，GA 考古準備 → GA midterm',
]


def initialize(setup, *, past=False, minutes=60):
    planner, first, calendar, bot = setup
    second = planner.db.create_task('Study ISPM ch2', NOW + timedelta(days=4), 'ISPM', NOW, {})
    third = planner.db.create_task('Study ISPM ch1', NOW + timedelta(days=4), 'ISPM', NOW, {})
    a = NOW + timedelta(days=-1 if past else 1)
    b = a + timedelta(minutes=60)
    old = {'id': 'oldstudy', 'etag': 'v1', 'summary': first['title'], '_calendar_id': 'primary',
        'creator': {'email': planner.settings.calendar_account}, 'organizer': {'self': True},
        'start': {'dateTime': a.isoformat()}, 'end': {'dateTime': b.isoformat()},
        'extendedProperties': {'private': {'chronosTask': str(first['id']), 'chronosToken': 'oldtoken'}}}
    events = {'oldstudy': old}
    planner.update(first['id'], lambda old: {'status': 'awaiting_feedback' if past else 'scheduled',
        'token': 'oldtoken', 'event_id': 'oldstudy', 'snapshot': task_fields(first),
        'start': a.isoformat(), 'end': b.isoformat(), 'minutes': 60})
    planner.db.mutate_task_action_state('learning:day:' + a.date().isoformat(), lambda old: {'oldtoken': 60})
    output = reset.ResetOutput(actionable=True, include_past=True,
        tasks=[reset.StudyChoice(task_id=t['id'], is_study=True, minutes=minutes) for t in (first, second, third)])
    planner.ai = AsyncMock()
    planner.ai.parse_study_reset.return_value = output
    calendar.agenda.side_effect = lambda start, end: [deepcopy(e) for e in events.values()
        if reset.bounds(e, planner.settings.tz)[0] < end and reset.bounds(e, planner.settings.tz)[1] > start]
    calendar.task_events.side_effect = lambda i: [deepcopy(e) for e in events.values()
        if e.get('extendedProperties', {}).get('private', {}).get('chronosTask') == str(i)]
    calendar.event.side_effect = lambda i: deepcopy(events.get(i))
    async def delete(i, etag):
        assert events[i]['etag'] == etag
        del events[i]
    async def insert(body):
        assert body['id'] not in events
        events[body['id']] = {**deepcopy(body), 'etag': 'new', '_calendar_id': 'primary',
            'creator': {'email': planner.settings.calendar_account}, 'organizer': {'self': True}}
        return deepcopy(events[body['id']])
    async def busy(a, b):
        return [reset.bounds(e, planner.settings.tz) for e in events.values()
            if reset.bounds(e, planner.settings.tz)[0] < b and reset.bounds(e, planner.settings.tz)[1] > a]
    calendar.delete_event.side_effect = delete
    calendar.insert.side_effect = insert
    calendar.busy.side_effect = busy
    return planner, events, output, first, second, third


def rebuild(planner, text=INPUTS[0], update=1, now=NOW):
    return asyncio.run(reset.run(planner, 123, text, update, now))


def test_rebuild_deletes_old_includes_unbooked_and_preserves_chapter_order(setup):
    planner, events, output, first, second, third = initialize(setup)
    reply = rebuild(planner)
    state = planner.db.get_task_action_state(reset.KEY)
    assert state['status'] == 'completed', reply
    assert 'oldstudy' not in events and len(events) == 3
    sessions = {s['task_id']: s for s in state['sessions']}
    assert sessions[third['id']]['end'] <= sessions[second['id']]['start']
    assert planner.get(first['id'])['event_id'] != 'oldstudy'
    assert all(planner.db.get_task(i)['status'] == 'open' for i in sessions)
    before = deepcopy(events)
    assert rebuild(planner) == reply
    assert events == before and planner.calendar.delete_event.await_count == 1
    assert planner.calendar.insert.await_count == 3
    assert 'oldtoken' not in planner.db.get_task_action_state('learning:day:' + (NOW + timedelta(days=1)).date().isoformat())


def test_past_unknown_scope_asks_once_and_answer_retains_request(setup):
    planner, events, output, *_ = initialize(setup, past=True)
    output.include_past = None
    assert '過去的讀書時段也要一起重排嗎' in rebuild(planner)
    assert planner.db.get_task_action_state(reset.KEY)['status'] == 'question'
    planner.calendar.delete_event.assert_not_awaited()
    output.include_past = True
    assert '已重新安排好' in rebuild(planner, '包含', 2)
    context = planner.ai.parse_study_reset.call_args.args[3]
    assert context['clarification']['instruction'] == INPUTS[0]


def test_future_only_retains_expired_event(setup):
    planner, events, output, *_ = initialize(setup, past=True)
    output.include_past = False
    assert '已重新安排好' in rebuild(planner)
    assert 'oldstudy' in events
    planner.calendar.delete_event.assert_not_awaited()


@pytest.mark.parametrize('failure', ['cycle', 'missing_task', 'foreign_event', 'active_write', 'ambiguous', 'ai_failure'])
def test_uncertainty_prevents_all_calendar_mutations(setup, failure):
    planner, events, output, first, second, third = initialize(setup)
    if failure == 'cycle':
        output.dependencies = [reset.Dependency(before=second['id'], after=third['id'])]
    elif failure == 'missing_task':
        output.tasks.pop()
    elif failure == 'foreign_event':
        events['oldstudy']['attendees'] = [{'email': 'other@example.com'}]
    elif failure == 'active_write':
        planner.update(first['id'], lambda old: {**old, 'status': 'creating'})
    elif failure == 'ambiguous':
        output.question = 'Which exam?'
    else:
        planner.ai.parse_study_reset.side_effect = AIError('unavailable')
    before = deepcopy(events)
    reply = rebuild(planner)
    assert '行程沒有變動' in reply and events == before
    planner.calendar.insert.assert_not_awaited()
    planner.calendar.delete_event.assert_not_awaited()


def test_compression_preserves_fixed_exam_and_rejects_impossible_plan(setup):
    planner, events, output, first, *_ = initialize(setup, minutes=180)
    output.start = NOW.replace(hour=18).isoformat()
    output.end = NOW.replace(hour=21).isoformat()
    # Three tasks cannot fit the evening at original durations; shorter sessions can.
    planner.settings.learning_daily_minutes = 720
    assert '已重新安排好' in rebuild(planner)
    state = planner.db.get_task_action_state(reset.KEY)
    assert state['factor'] < 1
    assert all(s['original_minutes'] == 180 and s['minutes'] >= 10 for s in state['sessions'])


def test_impossible_plan_never_deletes_original(setup):
    planner, events, output, *_ = initialize(setup)
    output.end = (NOW + timedelta(minutes=11)).isoformat()
    before = deepcopy(events)
    reply = rebuild(planner)
    assert '仍無法在期限前排完' in reply and events == before
    planner.calendar.delete_event.assert_not_awaited()


def test_lost_insert_response_recovers_without_duplicate_events(setup):
    planner, events, output, *_ = initialize(setup)
    insert = planner.calendar.insert.side_effect
    async def lost(body):
        await insert(body)
        raise CalendarError('response lost')
    planner.calendar.insert.side_effect = lost
    assert '重排暫停' in rebuild(planner)
    assert len(events) == 1
    planner.calendar.insert.side_effect = insert
    assert '已重新安排好' in rebuild(planner, update=2)
    assert len(events) == 3 and planner.calendar.insert.await_count == 3


def test_lost_delete_response_recovers_and_background_worker_waits(setup):
    planner, events, output, *_ = initialize(setup)
    delete = planner.calendar.delete_event.side_effect
    async def lost(i, etag):
        await delete(i, etag)
        raise CalendarError('response lost')
    planner.calendar.delete_event.side_effect = lost
    assert '重排暫停' in rebuild(planner)
    assert asyncio.run(planner._tick(NOW)) == {'waiting_for': 'study rebuild reconciliation'}
    assert '已重新安排好' in rebuild(planner, update=2)
    assert planner.calendar.delete_event.await_count == 1 and len(events) == 3


def test_exam_is_fixed_and_all_preparation_finishes_before_it(setup):
    planner, events, output, first, *_ = initialize(setup)
    a = NOW + timedelta(days=1)
    exam = {'id': 'exam', 'summary': 'Graphs midterm', 'etag': 'exam-v1', '_calendar_id': 'primary',
        'start': {'dateTime': a.isoformat()}, 'end': {'dateTime': (a+timedelta(hours=2)).isoformat()}}
    events['exam'] = deepcopy(exam)
    output.exams = [reset.ExamAnchor(task_id=first['id'], event_id='exam')]
    reply = rebuild(planner)
    assert '已重新安排好' in reply
    assert events['exam'] == exam
    assert planner.get(first['id'])['end'] <= a.isoformat()
    assert planner.db.get_task(first['id'])['due_at'] == first['due_at']


def test_multi_session_work_keeps_all_effort_and_does_not_infer_completion(setup):
    planner, events, output, first, *_ = initialize(setup, minutes=300)
    assert '已重新安排好' in rebuild(planner)
    state = planner.get(first['id'])
    assert sum(s['minutes'] for s in [state, *state['reset_remaining_sessions']]) == 300
    later = asyncio.run(reset.advance_sessions(planner, first, state, NOW + timedelta(days=1)))
    assert later['event_id'] != state['event_id']
    assert planner.db.get_task(first['id'])['status'] == 'open'


@pytest.mark.parametrize('text', INPUTS)
def test_ten_user_inputs_reach_reset_before_ordered_route(store, monkeypatch, text):
    monkeypatch.setattr(main.settings, 'enable_learning_calendar', True)
    execute = AsyncMock(return_value='rebuild result')
    monkeypatch.setattr(reset, 'run', execute)
    classify_mock = classify(monkeypatch, action='study_reset')
    assert run(text)['reply'] == 'rebuild result'
    execute.assert_awaited_once()
    # Some formulations deliberately defer to the model instead of unsafe keyword routing.
    assert classify_mock.await_count in (0, 1)


@pytest.mark.parametrize('text', ['不要把所有 study 行程重排', '如何重排所有讀書行程？',
    '把 ISPM ch1 移到明天', '「全部 study 重排」是什麼意思', 'do not reset all study sessions'])
def test_negative_and_single_task_inputs_do_not_fast_route(text):
    assert not reset.looks_reset(text)


def test_calendar_delete_uses_version_and_accepts_empty_success(setup):
    planner, *_ = setup
    seen = []
    async def handler(request):
        seen.append(request)
        return httpx.Response(204)
    calendar = GoogleCalendar(planner.settings, transport=httpx.MockTransport(handler))
    calendar.token = AsyncMock(return_value='test-token')
    assert asyncio.run(calendar.delete_event('event-id', 'v1')) == {}
    assert seen[0].method == 'DELETE' and seen[0].headers['if-match'] == 'v1'
    assert seen[0].url.params['sendUpdates'] == 'none'


@pytest.mark.parametrize('text', INPUTS)
def test_ten_scenarios_execute_real_rebuild_through_message_router(setup, monkeypatch, text):
    planner, events, output, *_ = initialize(setup)
    from chronos import intents
    class Clock(reset.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(intents, 'datetime', Clock)
    monkeypatch.setattr(main, 'db', planner.db)
    monkeypatch.setattr(main, 'tasks', TaskService(planner.db, planner.settings.tz))
    monkeypatch.setattr(main, 'settings', planner.settings)
    monkeypatch.setattr(main, 'learning_calendar', lambda: planner)
    monkeypatch.setattr(main, 'ai', planner.ai)
    planner.ai.classify_intent.return_value = Intent(action='study_reset')
    reply = run(text)
    assert '已重新安排好' in reply['reply']
    assert 'oldstudy' not in events and len(events) == 3
    planner.ai.classify_intent.assert_not_awaited()
    planner.ai.parse_study_reset.assert_awaited_once()


@pytest.mark.parametrize('changed', ['task', 'event', 'conflict', 'capacity', 'exam', 'identity'])
def test_preflight_race_never_deletes_old_events(setup, changed):
    planner, events, output, first, *_ = initialize(setup)
    original_event = planner.calendar.event.side_effect
    changed_once = False
    async def event(i):
        nonlocal changed_once
        state = planner.db.get_task_action_state(reset.KEY) or {}
        if state.get('status') == 'applying' and not changed_once:
            changed_once = True
            if changed == 'task':
                planner.db.edit_task(first['id'], 'Changed title', None, first['project'], {})
            elif changed == 'event':
                events['oldstudy']['etag'] = 'changed'
            elif changed == 'capacity':
                planner.settings.learning_daily_minutes = 90
            elif changed == 'identity':
                session = state['sessions'][0]
                events[session['event_id']] = {**deepcopy(events['oldstudy']), 'id': session['event_id']}
        return original_event(i)
    planner.calendar.event.side_effect = event
    original_agenda = planner.calendar.agenda.side_effect
    async def agenda(a,b):
        state = planner.db.get_task_action_state(reset.KEY) or {}
        if state.get('status') == 'applying' and changed == 'conflict':
            session = state['sessions'][0]
            events['new-conflict'] = {'id': 'new-conflict', '_calendar_id': 'primary',
                'start': {'dateTime': session['start']}, 'end': {'dateTime': session['end']}}
        if state.get('status') == 'applying' and changed == 'exam':
            events['exam']['etag'] = 'changed'
        return original_agenda(a,b)
    planner.calendar.agenda.side_effect = agenda
    if changed == 'exam':
        a = NOW + timedelta(days=1)
        events['exam'] = {'id': 'exam', 'summary': 'graphs midterm', 'etag': 'original', '_calendar_id': 'primary',
            'start': {'dateTime': a.isoformat()}, 'end': {'dateTime': (a + timedelta(hours=1)).isoformat()}}
        output.exams = [reset.ExamAnchor(task_id=first['id'], event_id='exam')]
    assert '重排暫停' in rebuild(planner)
    assert 'oldstudy' in events
    planner.calendar.delete_event.assert_not_awaited()
    planner.calendar.insert.assert_not_awaited()


def test_exact_estimated_effort_is_not_lost_to_model_underestimate(setup):
    planner, events, output, first, *_ = initialize(setup)
    planner.db.edit_task(first['id'], first['title'], reset.datetime.fromisoformat(first['due_at']), first['project'], {'estimated_minutes': 300})
    assert '已重新安排好' in rebuild(planner)
    state = planner.db.get_task_action_state(reset.KEY)
    assert sum(s['minutes'] for s in state['sessions'] if s['task_id'] == first['id']) == 300


def test_cancel_scope_question_keeps_original_bookings(setup):
    planner, events, output, *_ = initialize(setup, past=True)
    output.include_past = None
    rebuild(planner)
    assert reset.pending_reply(planner.db.get_task_action_state(reset.KEY), '/cancel')
    assert '已取消' in rebuild(planner, '/cancel', 2)
    assert planner.db.get_task_action_state(reset.KEY)['status'] == 'cancelled'
    assert 'oldstudy' in events


@pytest.mark.parametrize('text', [INPUTS[7], '/studyreset', '/studyreset@ChronosBot 包含過期未完成的行程'])
def test_webhook_rebuild_precedes_existing_edit_context_and_replay_is_idempotent(setup, monkeypatch, text):
    planner, events, output, *_ = initialize(setup)
    from chronos import calendar_replan
    class Clock(reset.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(reset, 'datetime', Clock)
    monkeypatch.setattr(main, 'settings', planner.settings)
    monkeypatch.setattr(main, 'db', planner.db)
    monkeypatch.setattr(main, 'tasks', TaskService(planner.db, planner.settings.tz))
    monkeypatch.setattr(main, 'learning_calendar', lambda: planner)
    monkeypatch.setattr(main, 'telegram', planner.telegram)
    planner.db.mutate_task_action_state(calendar_replan.key(123), lambda old:
        {'status': 'editing', 'ordered_sequence': True, 'expires_at': '2099-01-01T00:00:00+08:00'})
    async def replay():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            message = {'message_id': 1, 'chat': {'id': 123}, 'text': text}
            assert (await client.post('/telegram/webhook', json={'update_id': 1, 'message': message})).status_code == 200
            assert (await client.post('/telegram/webhook', json={'update_id': 1, 'message': message})).status_code == 200
            assert (await client.post('/telegram/webhook', json={'update_id': 2, 'edited_message': message})).status_code == 200
    asyncio.run(replay())
    assert 'oldstudy' not in events and len(events) == 3
    assert planner.calendar.insert.await_count == 3
    planner.ai.parse_study_reset.assert_awaited_once()


def test_existing_in_progress_review_blocks_reset(setup):
    planner, events, output, *_ = initialize(setup)
    from chronos import calendar_replan
    planner.db.mutate_task_action_state(calendar_replan.key(123), lambda old: {'status': 'creating'})
    assert '先完成' in rebuild(planner)
    planner.calendar.delete_event.assert_not_awaited()
    planner.calendar.insert.assert_not_awaited()


def test_parser_requests_validated_schema_and_supplies_owner_constraints(setup, monkeypatch):
    from chronos.ai import ExternalAI
    planner, events, output, *_ = initialize(setup)
    ai = ExternalAI(planner.settings)
    generate = AsyncMock(return_value=output)
    monkeypatch.setattr(ai, '_generate_output', generate)
    parsed = asyncio.run(ai.parse_study_reset(INPUTS[0], [], [], {'constraints': {}}, NOW))
    assert parsed == output
    args = generate.call_args.args
    schema = reset.ResetOutput.model_json_schema()
    for name in ('tasks', 'dependencies', 'exams'):
        del schema['properties'][name]['maxItems']
    assert args[2] == schema and args[3] is reset.ResetOutput
    assert 'Exam events remain FIXED' in args[0]


@pytest.mark.parametrize('text,args', [
    ('/studyreset', ''), ('/studyreset  ', ''), ('/studyreset@ChronosBot', ''),
    ('/studyreset 包含過期未完成的行程', '包含過期未完成的行程'),
    ('/studyreset@ChronosBot 只排未來', '只排未來'),
    ('/studyresetting', None), ('說明 /studyreset', None),
])
def test_dedicated_command_has_exact_boundary_and_bot_mention(text, args):
    assert reset.command_args(text) == args


@pytest.mark.parametrize('text', ['/studyreset', '/studyreset@ChronosBot 只排未來'])
def test_dedicated_command_routes_without_classification(store, monkeypatch, text):
    monkeypatch.setattr(main.settings, 'enable_learning_calendar', True)
    service = AsyncMock(return_value='reset result')
    monkeypatch.setattr(reset, 'run', service)
    classifier = classify(monkeypatch, action='unsupported')
    assert run(text)['reply'] == 'reset result'
    service.assert_awaited_once()
    instruction = service.call_args.args[2]
    assert 'Rebuild all unfinished study sessions' in instruction
    if '只排未來' in text:
        assert '只排未來' in instruction
    classifier.assert_not_awaited()


def test_dedicated_command_help_does_not_call_ai_or_change_calendar(setup):
    planner, events, output, *_ = initialize(setup)
    before = deepcopy(events)
    assert asyncio.run(reset.command(planner, 123, '/studyreset help', 1, NOW)) == reset.COMMAND_HELP
    assert events == before
    planner.ai.parse_study_reset.assert_not_awaited()
    planner.calendar.delete_event.assert_not_awaited()
    planner.calendar.insert.assert_not_awaited()


def test_dedicated_command_disabled_and_unbound_do_not_execute(store, monkeypatch):
    monkeypatch.setattr(main.settings, 'enable_learning_calendar', False)
    execute = AsyncMock()
    monkeypatch.setattr(reset, 'run', execute)
    assert 'not enabled' in run('/studyreset')['reply']
    reply = asyncio.run(main.prepare_message('/studyreset'))()
    assert 'Telegram' in reply
    execute.assert_not_awaited()

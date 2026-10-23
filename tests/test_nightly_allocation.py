import asyncio
from datetime import datetime, timedelta
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from chronos.ai import AIError, ExternalAI, NightlyPlanOutput, NightlySession
from chronos.nightly_planning import eligible, free_windows, validate
from chronos.settings import Settings
from chronos.google_calendar import CalendarError
from test_ai import config, mock_provider
from test_nightly_planning import setup, NIGHT
from test_learning_calendar import setup as base_setup


CFG = Settings(_env_file=None)
WED = datetime.fromisoformat('2026-10-21T00:00:00+08:00')
THU = WED + timedelta(days=1)


@pytest.mark.parametrize(('day', 'expected'), [(WED, [('16:00', '17:00'), ('18:00', '21:00')]),
    (THU, [('13:00', '17:00')])])
def test_user_screenshot_availability(day, expected):
    result = free_windows(day.date(), [], CFG)
    assert [(a.strftime('%H:%M'), b.strftime('%H:%M')) for a, b in result] == expected


def test_thirty_minutes_is_a_gap_threshold_not_an_alignment_rule():
    cfg = Settings(_env_file=None, learning_class_preparation_minutes=0)
    slots = free_windows(THU.date(), [], cfg)
    assert (THU.replace(hour=18), THU.replace(hour=18, minute=30)) in slots
    busy = [(WED.replace(hour=16, minute=29), WED.replace(hour=17)),
            (WED.replace(hour=18, minute=30), WED.replace(hour=21))]
    assert free_windows(WED.date(), busy, CFG) == [(WED.replace(hour=18), WED.replace(hour=18, minute=30))]


def test_adjacent_and_overlapping_commitments_are_merged():
    busy = [(THU.replace(hour=13), THU.replace(hour=14)),
            (THU.replace(hour=13, minute=30), THU.replace(hour=15)),
            (THU.replace(hour=15), THU.replace(hour=16))]
    assert free_windows(THU.date(), busy, CFG) == [(THU.replace(hour=16), THU.replace(hour=17))]


def test_legacy_personal_event_is_not_allocated_as_study_work():
    assert not eligible({'title': 'Participate in AtCoder contest',
        'timing': {'event': '2026-10-24T20:00:00+08:00'}}, None, NIGHT)


def sessions_for(tasks, start, durations):
    result = []
    for task, minutes in zip(tasks, durations):
        end = start + timedelta(minutes=minutes)
        result.append(NightlySession(task_id=task['id'], start=start, end=end,
            estimated_minutes=minutes, reason='依工作內容安排所需時長。'))
        start = end
    return NightlyPlanOutput(sessions=result)


def test_one_opening_accepts_variable_task_lengths_without_forced_switches():
    begin = WED.replace(hour=9)
    tasks = [{'id': i} for i in (1, 2, 3)]
    output = sessions_for(tasks, begin, [45, 70, 65])
    result = validate(output, tasks, [(begin, begin + timedelta(hours=3))], 180, CFG)
    assert [int((s.end - s.start).total_seconds() / 60) for s in result] == [45, 70, 65]
    long = sessions_for(tasks[:1], begin, [180])
    assert len(validate(long, tasks, [(begin, begin + timedelta(hours=3))], 180, CFG)) == 1


@pytest.mark.parametrize('case', ['unknown_id', 'duplicate', 'overlap', 'outside_window', 'naive',
    'budget', 'deadline', 'overestimate_allocation', 'seconds'])
def test_invalid_ai_allocations_are_rejected(case):
    begin = THU.replace(hour=13)
    tasks = [{'id': 1}, {'id': 2}]
    output = sessions_for(tasks, begin, [45, 70])
    budget = 240
    if case == 'unknown_id': output.sessions[0].task_id = 99
    if case == 'duplicate': output.sessions[1].task_id = 1
    if case == 'overlap': output.sessions[1].start = begin
    if case == 'outside_window': output.sessions[0].start -= timedelta(minutes=1)
    if case == 'naive': output.sessions[0].start = begin.replace(tzinfo=None)
    if case == 'budget': budget = 60
    if case == 'deadline': tasks[0]['due_at'] = (begin + timedelta(minutes=30)).isoformat()
    if case == 'overestimate_allocation': output.sessions[0].estimated_minutes = 30
    if case == 'seconds': output.sessions[0].end += timedelta(seconds=1)
    with pytest.raises(ValueError):
        validate(output, tasks, [(begin, begin + timedelta(hours=4))], budget, CFG)


def test_nightly_keeps_exact_non_grid_minutes_through_approval(setup):
    planner, task, calendar, bot = setup
    second = planner.db.create_task('Second task', None, None, NIGHT)
    start = (NIGHT + timedelta(days=1)).replace(hour=9, minute=0)
    planner.ai.plan_tomorrow.side_effect = None
    planner.ai.plan_tomorrow.return_value = sessions_for([task, second], start, [45, 70])
    result = asyncio.run(planner.nightly_proposals(NIGHT))
    assert result['nightly_task_ids'] == [task['id'], second['id']]
    first = planner.get(task['id'])
    assert 'Tomorrow' in bot.send_message.await_args.args[1]
    assert first['minutes'] == 45
    asyncio.run(planner.callback('approve', task['id'], first['token'], first['message_id']))
    asyncio.run(planner.tick(NIGHT))
    other = planner.get(second['id'])
    assert other['minutes'] == 70 and other['start'] == (start + timedelta(minutes=45)).isoformat()
    asyncio.run(planner.callback('approve', second['id'], other['token'], other['message_id']))
    assert calendar.insert.await_count == 2


def test_no_complete_half_hour_skips_ai_and_calendar_writes(setup):
    planner, task, calendar, bot = setup
    begin = (NIGHT + timedelta(days=1)).replace(hour=9, minute=0)
    calendar.busy.return_value = [(begin + timedelta(minutes=29), begin.replace(hour=21))]
    result = asyncio.run(planner.nightly_proposals(NIGHT))
    assert result['nightly_task_ids'] == []
    planner.ai.plan_tomorrow.assert_not_awaited()
    assert planner.get(task['id']) is None
    calendar.insert.assert_not_awaited()


def test_ai_failure_leaves_no_partial_task_queue_and_can_retry(setup):
    planner, task, calendar, bot = setup
    allocate = planner.ai.plan_tomorrow.side_effect
    planner.ai.plan_tomorrow.side_effect = AIError('Unavailable')
    with pytest.raises(CalendarError, match='not saved'):
        asyncio.run(planner.nightly_proposals(NIGHT))
    assert planner.get(task['id']) is None
    planner.ai.plan_tomorrow.side_effect = allocate
    asyncio.run(planner.nightly_proposals(NIGHT + timedelta(minutes=1)))
    assert planner.get(task['id'])['status'] == 'proposed'
    calendar.insert.assert_not_awaited()


def test_new_calendar_conflict_never_books_or_spills_to_another_day(setup):
    planner, task, calendar, bot = setup
    asyncio.run(planner.nightly_proposals(NIGHT))
    state = planner.get(task['id'])
    a, b = (datetime.fromisoformat(state[k]) for k in ('start', 'end'))
    calendar.busy.return_value = [(a, b)]
    reply = asyncio.run(planner.callback('approve', task['id'], state['token'], state['message_id']))
    assert 'No event' in reply
    assert planner.get(task['id'])['status'] == 'needs_replan'
    calendar.insert.assert_not_awaited()


def test_same_daily_receipt_does_not_repeat_ai_generation(setup):
    planner, task, calendar, bot = setup
    asyncio.run(planner.nightly_proposals(NIGHT))
    asyncio.run(planner.nightly_proposals(NIGHT + timedelta(minutes=2)))
    planner.ai.plan_tomorrow.assert_awaited_once()
    assert bot.send_message.await_count == 1


def test_concurrent_daily_claim_does_not_generate(setup):
    planner, task, calendar, bot = setup
    planner.db.mutate_task_action_state('learning:nightly:' + NIGHT.date().isoformat(), lambda old:
        {'status': 'planning', 'claim': 'other', 'lease_until': (NIGHT + timedelta(minutes=2)).isoformat()})
    assert asyncio.run(planner.nightly_proposals(NIGHT))['already_running']
    planner.ai.plan_tomorrow.assert_not_awaited()
    assert planner.get(task['id']) is None


def test_task_change_during_ai_generation_leaves_no_partial_queue(setup):
    planner, task, calendar, bot = setup
    original = planner.ai.plan_tomorrow.side_effect
    async def changed(*args):
        result = await original(*args)
        planner.db.edit_task(task['id'], 'Changed during generation', None, None, {})
        return result
    planner.ai.plan_tomorrow.side_effect = changed
    with pytest.raises(CalendarError, match='Tasks changed'):
        asyncio.run(planner.nightly_proposals(NIGHT))
    assert planner.get(task['id']) is None
    calendar.insert.assert_not_awaited()


def test_explicit_plan_request_releases_expired_nightly_anchor(setup):
    planner, task, calendar, bot = setup
    asyncio.run(planner.nightly_proposals(NIGHT))
    planner.request_proposal(task['id'], immediate=True)
    state = planner.get(task['id'])
    assert 'nightly_slot' not in state and state.get('duration_source') != 'nightly_ai'


def test_invalid_whole_day_result_saves_no_session(setup):
    planner, task, calendar, bot = setup
    second = planner.db.create_task('Second', None, None, NIGHT)
    start = (NIGHT + timedelta(days=1)).replace(hour=9, minute=0)
    output = sessions_for([task, second], start, [45, 70])
    output.sessions[1].task_id = 99999
    planner.ai.plan_tomorrow.side_effect = None
    planner.ai.plan_tomorrow.return_value = output
    with pytest.raises(CalendarError, match='Unknown'):
        asyncio.run(planner.nightly_proposals(NIGHT))
    assert planner.get(task['id']) is None and planner.get(second['id']) is None
    calendar.insert.assert_not_awaited()


def test_owner_model_transport_contract(monkeypatch):
    start = THU.replace(hour=13)
    def handler(request):
        data = json.loads(request.content)
        prompt = data['systemInstruction']['parts'][0]['text']
        payload = json.loads(data['contents'][0]['parts'][0]['text'])
        assert '30 is NOT' in prompt and 'never claim Calendar' in prompt
        assert payload['budget_minutes'] == 120
        result = sessions_for([{'id': 1}], start, [70])
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': result.model_dump_json()}]}}]})
    mock_provider(monkeypatch, handler)
    result = asyncio.run(ExternalAI(config()).plan_tomorrow([{'id': 1, 'title': 'Study'}],
        [{'start': start.isoformat(), 'end': (start + timedelta(hours=2)).isoformat()}], 120, {}))
    assert result.sessions[0].estimated_minutes == 70

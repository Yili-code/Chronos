import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import main
from chronos.ai import AIError, ExternalAI, FieldEdit, TaskEditOutput
from chronos.free_booking import EVENT_COLORS, bind_free_message, free_booking_callback
from chronos.intents import explicit_event_minutes
from chronos.settings import Settings
from chronos.tasks import ParsedTask
from test_intents import store, run, classify


def test_event_preview_preserves_three_hours_without_task(store, monkeypatch):
    now = datetime.now(main.settings.tz)
    start = (now + timedelta(days=3)).replace(hour=20, minute=0, second=0, microsecond=0)
    classify(monkeypatch, action='event_create', title='AtCoder contest', start=start,
             minutes=180, event_category='contest', content='Estimated duration: 3h')
    result = run('10/24 20:00 atcoder contest 預計 3h')
    message = result['messages'][0]
    assert '180 min' in message['text'] and '23:00' in message['text']
    assert store.list_open_tasks() == []
    main.ai.parse.assert_not_awaited()
    bind_free_message(store, 123, 20, message)
    calendar = AsyncMock()
    calendar.event.return_value = None
    calendar.busy.return_value = []
    calendar.insert.side_effect = lambda body: body
    reply = asyncio.run(free_booking_callback(store, calendar, main.settings, 123, 20,
        message['free_booking'], 'allow', now))
    assert 'Added to Calendar' in reply
    body = calendar.insert.await_args.args[0]
    assert body['colorId'] == EVENT_COLORS['contest']
    assert 'Estimated duration: 3h' in body['description']
    assert '10/24 20:00 atcoder contest 預計 3h' in body['description']
    assert datetime.fromisoformat(body['end']['dateTime']) - datetime.fromisoformat(body['start']['dateTime']) == timedelta(hours=3)
    asyncio.run(free_booking_callback(store, calendar, main.settings, 123, 20,
        message['free_booking'], 'allow', now))
    calendar.insert.assert_awaited_once()
    assert store.list_open_tasks() == []


@pytest.mark.parametrize('case', ['missing_duration', 'naive', 'inconsistent', 'past'])
def test_invalid_event_never_silently_becomes_task(store, monkeypatch, case):
    start = datetime.now(main.settings.tz) + timedelta(days=2)
    args = dict(action='event_create', title='Meeting', start=start, minutes=60, event_category='meeting')
    if case == 'missing_duration': args.pop('minutes')
    if case == 'naive': args['start'] = start.replace(tzinfo=None)
    if case == 'inconsistent': args['end'] = start + timedelta(hours=3)
    if case == 'past': args['start'] = start - timedelta(days=3)
    classify(monkeypatch, **args)
    assert '請' in run('安排會議')['reply']
    assert store.list_open_tasks() == []


def test_content_clarification_keeps_original_target_and_instruction(store, monkeypatch):
    task = main.tasks.create('Complete competitive programming assignment')
    classify(monkeypatch, action='clarify', task_ids=[task['id']],
             instruction='task 1 的 competitive programming 剩下一題程式題', question='要修改標題或內容？')
    run('task 1 的 competitive programming 剩下一題程式題')
    # Even a mistaken followup classification cannot turn the chosen content field into a title edit.
    classify(monkeypatch, action='clarify', question='哪個任務？')
    edit = AsyncMock(return_value=ParsedTask(task['title'], timing={'content': 'One programming problem remaining'}))
    monkeypatch.setattr(main.ai, 'edit', edit)
    result = run('修改他的內容', update=2)
    instruction = edit.await_args.args[1]
    assert '剩下一題程式題' in instruction and 'Only change content' in instruction
    saved = main.tasks.list_open()[0]
    assert saved['title'] == task['title']
    assert saved['timing']['content'] == 'One programming problem remaining'
    assert '<b>Before</b>' in result['reply'] and '<b>After</b>' in result['reply']
    assert '<b>Content</b>' in result['reply']
    assert 'One programming problem remaining' in main.format_tasks([saved], main.settings.tz)
    store.undo_task_edit()
    restored = main.tasks.list_open()[0]
    assert restored['title'] == task['title'] and not restored['timing'].get('content')


def test_ai_content_edit_rejects_title_mutation(monkeypatch):
    ai = ExternalAI(Settings(_env_file=None))
    fields = {k: FieldEdit(op='keep', value=None) for k in TaskEditOutput.model_fields}
    fields['title'] = FieldEdit(op='set', value='Wrong title')
    monkeypatch.setattr(ai, '_generate_output', AsyncMock(return_value=TaskEditOutput(**fields)))
    with pytest.raises(AIError, match='preserve'):
        asyncio.run(ai.edit({'title': 'Stable title'}, '修改內容為剩下一題'))


def test_categories_have_distinct_colors():
    assert len(set(EVENT_COLORS.values())) == len(EVENT_COLORS)


@pytest.mark.parametrize(('text', 'minutes'), [('預計 3h', 180), ('1.5 hours', 90),
    ('1小時30分鐘', 90), ('20:00 meeting 45 min', 45), ('20:00 contest', None)])
def test_numeric_duration(text, minutes):
    assert explicit_event_minutes(text) == minutes


def test_explicit_duration_cannot_be_silently_shortened(store, monkeypatch):
    start = datetime.now(main.settings.tz) + timedelta(days=2)
    classify(monkeypatch, action='event_create', title='Contest', start=start, minutes=120, event_category='contest')
    assert '不一致' in run('contest 預計 3h')['reply']
    assert store.list_open_tasks() == []


def test_ai_content_operation_preserves_unrelated_fields(monkeypatch):
    ai = ExternalAI(Settings(_env_file=None))
    fields = {k: FieldEdit(op='keep', value=None) for k in TaskEditOutput.model_fields}
    fields['content'] = FieldEdit(op='set', value='One problem remaining')
    monkeypatch.setattr(ai, '_generate_output', AsyncMock(return_value=TaskEditOutput(**fields)))
    parsed = asyncio.run(ai.edit({'title': 'Stable title', 'project': 'course',
        'timing': {'due_date': '2026-11-01', 'content': 'Three problems remaining'}}, '修改內容為剩下一題'))
    assert parsed.title == 'Stable title' and parsed.project == 'course'
    assert parsed.timing['content'] == 'One problem remaining'
    assert parsed.timing['due_date'] == '2026-11-01'


def test_new_explicit_content_request_does_not_reuse_old_target(store, monkeypatch):
    old, new = main.tasks.create('Old'), main.tasks.create('New')
    classify(monkeypatch, action='clarify', task_ids=[old['id']], instruction='Old detail', question='標題或內容？')
    run('Old detail')
    classify(monkeypatch, action='task_edit', task_ids=[new['id']], instruction='修改 New 的內容為 new detail')
    edit = AsyncMock(return_value=ParsedTask('New', timing={'content': 'new detail'}))
    monkeypatch.setattr(main.ai, 'edit', edit)
    run('修改 New 的內容為 new detail', update=2)
    assert edit.await_args.args[0]['id'] == new['id']
    assert main.tasks.get_open_by_id(old['id'])['title'] == 'Old'

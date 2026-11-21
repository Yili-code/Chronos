"""Sequential synthetic today replies; captured live interpretation, real routing."""
import asyncio
import copy
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from chronos import main, intents, learning_calendar, evening_agenda
from chronos.ai import ExternalAI, ScheduleEditOutput
from chronos.db import Database
from chronos.intents import Intent
from chronos.learning_calendar import LearningCalendar
from chronos.settings import Settings
from chronos.task_actions import task_fields
from chronos.tasks import TaskService
from test_firestore import database as firestore_database

NOW = datetime.fromisoformat('2026-10-24T17:30:00+08:00')
DATA = Path(__file__).parent / 'fixtures' / 'today_responses'
CASES = [
    (1, 'Study ISPM Chapter 2 做完了', 'complete'),
    (2, '第一個提前到今天 17:55，維持一小時', 'move'),
    (3, '往後挪一點', 'clarify'),
    (4, '第一個改到', 'clarify'),
    (5, '取消第二個 Codeforces Round 1129', 'external'),
    (6, 'asdf ### 🧩 ???', 'reject'),
    (7, '第一個做完了', 'expired'),
    (8, '第一個做完了', 'stale'),
    (9, '這兩個都標記完成', 'batch'),
    (10, '第三個提前到今天 18:15', 'range'),
]


@pytest.fixture(params=['sqlite', 'firestore'])
def today_system(request, tmp_path, monkeypatch):
    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    for module in (main, intents, learning_calendar, evening_agenda):
        monkeypatch.setattr(module, 'datetime', Clock)
    cfg = Settings(_env_file=None, enable_learning_calendar=True, learning_auto_accept=True,
                   learning_enabled_since=NOW.isoformat(), enable_internal_scheduler=False,
                   telegram_chat_id=123, telegram_webhook_secret='test-hook', calendar_account='owner@example.com')
    db = firestore_database(monkeypatch) if request.param == 'firestore' else Database(tmp_path / 'today.db')
    db.initialize()
    # Global task order deliberately differs from the two-row agenda.
    hidden = db.create_task('Unrelated database report', None, None, NOW, {})
    task = db.create_task('Study ISPM Chapter 2', None, None, NOW, {})
    calendar, bot = AsyncMock(), AsyncMock()
    calendar.configured = lambda: True
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 50}}
    bot.request.return_value = {'ok': True}
    planner = LearningCalendar(db, calendar, bot, cfg, main.ai)
    state = {'status': 'scheduled', 'token': 'a' * 32, 'event_id': 'owned', 'minutes': 60,
             'start': '2026-10-24T18:00:00+08:00', 'end': '2026-10-24T19:00:00+08:00',
             'snapshot': task_fields(task), 'message_id': 40}
    planner.update(task['id'], lambda old: state)
    db.mutate_task_action_state('learning:day:2026-10-24', lambda old: {state['token']: 60})
    event = {'id': 'owned', 'summary': task['title'], 'etag': 'old', '_calendar_id': 'primary',
             'creator': {'email': cfg.calendar_account}, 'organizer': {'self': True},
             'start': {'dateTime': state['start']}, 'end': {'dateTime': state['end']},
             'extendedProperties': {'private': {'chronosTask': str(task['id']), 'chronosToken': state['token']}}}
    external = {'id': 'personal', 'summary': 'Codeforces Round 1129', '_calendar_id': 'primary',
                'start': {'dateTime': '2026-10-24T19:05:00+08:00'},
                'end': {'dateTime': '2026-10-24T21:05:00+08:00'}}
    calendar.event.return_value = event
    calendar.agenda.return_value = [event, external]
    calendar.busy.return_value = []

    async def move(event_id, body, etag):
        assert event_id == 'owned' and etag == event['etag']
        event.update(copy.deepcopy(body))
        event['etag'] = 'new'
        return event

    calendar.move_event.side_effect = move
    for name, value in [('settings', cfg), ('db', db), ('tasks', TaskService(db, cfg.tz)),
                        ('telegram', bot), ('google_calendar', calendar)]:
        monkeypatch.setattr(main, name, value)
    monkeypatch.setattr(main, 'learning_calendar', lambda: planner)
    # Any unexpected provider or mutation path fails the audit.
    monkeypatch.setattr(main.ai, 'parse_many', AsyncMock(side_effect=AssertionError('Unexpected task creation')))
    monkeypatch.setattr(main.ai, 'parse', AsyncMock(side_effect=AssertionError('Unexpected task edit')))
    return db, task, hidden, planner, calendar, bot, Clock, request.param


@pytest.mark.parametrize('case', CASES, ids=lambda case: 'case' + str(case[0]))
def test_today_response(today_system, monkeypatch, case):
    number, text, expected = case
    if os.getenv('TODAY_AUDIT_CASE') and number != int(os.environ['TODAY_AUDIT_CASE']):
        pytest.skip('Sequential case selector')
    db, task, hidden, planner, calendar, bot, clock, backend = today_system
    client = TestClient(main.app, raise_server_exceptions=True)
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}

    def post(update_id, text, reply=False):
        message = {'message_id': update_id, 'chat': {'id': 123}, 'text': text}
        if reply:
            message['reply_to_message'] = {'message_id': 50}
        response = client.post('/telegram/webhook', json={'update_id': update_id, 'message': message}, headers=headers)
        assert response.status_code == 200, response.text

    post(80000, '/today')
    assert '18:00–19:00\nStudy ISPM Chapter 2' in bot.send_message.await_args.args[1]
    if expected == 'expired':
        clock.current = NOW + timedelta(days=1)
    if expected == 'stale':
        db.edit_task(task['id'], 'Study ISPM Chapter 3', None, None, task.get('timing'))
    before = copy.deepcopy(db.get_task(task['id']))
    plan_before = copy.deepcopy(planner.get(task['id']))
    sample = DATA / f'{number}.json'
    record = json.loads(sample.read_text(encoding='utf-8')) if sample.exists() else {}
    live = os.getenv('TODAY_AUDIT_LIVE') == '1' and backend == 'sqlite'
    provider = None
    if live:
        provider = ExternalAI(Settings(_env_file=os.getenv('TODAY_AUDIT_ENV_FILE', '.env')))

    def interpreter(name, model):
        calls = 0
        async def run(*args, **kwargs):
            nonlocal calls
            calls += 1
            sample_key = name if calls == 1 else name + str(calls)
            if provider:
                result = await getattr(provider, name)(*args, **kwargs)
                record[sample_key] = result.model_dump(mode='json')
                DATA.mkdir(parents=True, exist_ok=True)
                sample.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                return result
            assert sample_key in record, f'No captured live {sample_key} for case {number}'
            return model.model_validate(record[sample_key])
        return AsyncMock(side_effect=run)

    classifier = interpreter('classify_intent', Intent)
    editor = interpreter('parse_schedule_edit', ScheduleEditOutput)
    monkeypatch.setattr(main.ai, 'classify_intent', classifier)
    monkeypatch.setattr(main.ai, 'parse_schedule_edit', editor)
    response_id = 81000 + number
    post(response_id, text, reply=True)
    reply = bot.send_message.await_args.args[1]
    assert '課後進度' not in reply
    if expected == 'complete':
        assert db.get_task(task['id'])['status'] == 'done', reply
        classifier.assert_awaited_once()
    elif expected == 'move':
        calendar.move_event.assert_not_awaited()
        assert 'unchanged' in reply and 'limits' in reply
        assert planner.get(task['id']) == plan_before
        text = '第一個改到今天 18:05，維持一小時'
        response_id = 82000 + number
        post(response_id, text, reply=True)
        reply = bot.send_message.await_args.args[1]
        assert calendar.move_event.await_count == 1, reply
        calendar.move_event.assert_awaited_once()
        assert planner.get(task['id'])['start'] == '2026-10-24T18:05:00+08:00', reply
        assert planner.get(task['id'])['minutes'] == 60
        assert '沒有變更' not in reply and 'unchanged' not in reply, reply
    else:
        assert db.get_task(task['id']) == before, reply
        assert planner.get(task['id']) == plan_before, reply
        calendar.move_event.assert_not_awaited()
        if expected == 'clarify':
            if number == 3:
                assert '哪一項' in reply and '時間' in reply
                classifier.assert_not_awaited()
            else:
                assert record['classify_intent']['action'] == 'clarify'
                assert record['classify_intent']['question']
        elif expected == 'external':
            assert 'Calendar' in reply and '不支援' in reply
        elif expected == 'reject':
            assert record['classify_intent']['action'] in {'clarify', 'unsupported'}
            assert reply
        else:
            assert any(word in reply for word in ('哪', '補充', '無法', '未', '重新', '過期', '變更', '沒有', '不支援')), reply
        if expected in {'expired', 'stale', 'range'}:
            classifier.assert_not_awaited()
    assert db.get_task(hidden['id'])['status'] == 'open'
    assert len(db.list_open_tasks()) == (1 if expected == 'complete' else 2)
    calendar.insert.assert_not_awaited()
    calendar.request.assert_not_awaited()
    sent = bot.send_message.await_count
    post(response_id, text, reply=True)
    assert bot.send_message.await_count == sent
    if provider:
        asyncio.run(provider.aclose()) if hasattr(provider, 'aclose') else None


def test_pagination_context_follows_exact_rendered_rows(today_system):
    db, task, hidden, planner, calendar, bot, clock, backend = today_system
    calendar.agenda.return_value = [
        {'id': str(index), 'summary': f'Event {index:02d} ' + 'x' * 400,
         'start': {'dateTime': '2026-10-24T18:00:00+08:00'},
         'end': {'dateTime': '2026-10-24T19:00:00+08:00'}} for index in reversed(range(20))
    ]
    messages = asyncio.run(evening_agenda.agenda_messages(calendar, main.settings, NOW, day_offset=0, db=db))
    assert len(messages) > 1
    rows = []
    for message in messages:
        titles = [entry['title'] for entry in message['today_agenda']['entries']]
        assert titles and all(title in message['text'] for title in titles)
        offsets = [message['text'].index(title) for title in titles]
        assert offsets == sorted(offsets)
        rows.extend(titles)
    assert len(rows) == 20 and len(set(rows)) == 20
    assert rows == sorted(rows)


@pytest.mark.parametrize('race', ['outside-target', 'during-interpretation', 'before-commit'])
def test_agenda_rechecks_authority_and_snapshot(today_system, monkeypatch, race):
    from chronos import agenda_context

    db, task, hidden, planner, calendar, bot, clock, backend = today_system
    context = agenda_context.snapshot(calendar.agenda.return_value, db, NOW, main.settings)

    def rename():
        db.edit_task(task['id'], 'Updated task content', None, None, task.get('timing'))

    async def classify(*args, **kwargs):
        if race == 'during-interpretation':
            rename()
        return Intent(action='task_complete', task_ids=[hidden['id'] if race == 'outside-target' else task['id']])

    monkeypatch.setattr(main.ai, 'classify_intent', AsyncMock(side_effect=classify))
    action = asyncio.run(agenda_context.prepare(main, context, 'Study ISPM Chapter 2 做完了', chat_id=123, update_id=90000))
    if race == 'before-commit':
        rename()
    receipt = db.process_update(90000, action)
    assert db.get_task(task['id'])['status'] == db.get_task(hidden['id'])['status'] == 'open'
    assert any(word in receipt['reply'] for word in ('變更', '重新'))


def test_refresh_command_in_agenda_reply_uses_normal_command_route(today_system):
    from chronos import agenda_context

    db, task, hidden, planner, calendar, bot, clock, backend = today_system
    context = agenda_context.snapshot(calendar.agenda.return_value, db, NOW, main.settings)
    action = asyncio.run(agenda_context.prepare(main, context, '/today', chat_id=123, update_id=90001))
    assert action()['messages'][0]['today_agenda']['date'] == NOW.date().isoformat()


def test_single_target_with_multiple_constraints_is_not_a_batch(today_system, monkeypatch):
    from chronos import agenda_context

    db, task, hidden, planner, calendar, bot, clock, backend = today_system
    context = agenda_context.snapshot(calendar.agenda.return_value, db, NOW, main.settings)
    classifier = AsyncMock(return_value=Intent(action='clarify', task_ids=[task['id']], question='請提供開始時間。'))
    monkeypatch.setattr(main.ai, 'classify_intent', classifier)
    action = asyncio.run(agenda_context.prepare(main, context, '第一個改到晚上，時間和原來一樣', chat_id=123, update_id=90002))
    classifier.assert_awaited_once()
    assert classifier.await_args.kwargs['context']['bound_task_id'] == task['id']
    assert action()['messages'][0]['text'].startswith('請提供開始時間。')

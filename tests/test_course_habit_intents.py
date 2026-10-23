import asyncio
import json
import httpx
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from chronos import course_habits as habits, main
from chronos.ai import AIError, ExternalAI
from scripts.audit_course_habits import CASES
from test_course_habits import db, start, NOW
from test_ai import config, mock_provider
from test_system import system


def result(action, first=None, second=None):
    return habits.HabitIntent(action=action, first=first, second=second)


@pytest.mark.parametrize('number,case', enumerate(CASES, 1), ids=[case[2] for case in CASES])
def test_ten_reply_contracts(db, number, case):
    text, expected, _ = case
    start(db)
    previous = list(habits.preference(db, 'competitive-programming'))
    first = previous[0] if number in {6, 7} else '複習本週內容'
    second = '完成至少十題程式題' if number == 6 else previous[1]
    model = AsyncMock()
    model._generate_output.return_value = result(expected, first, second) if expected == 'set' else result(expected)
    action = asyncio.run(habits.prepare_reply(db, model, 81, text))
    # progress is routed back to the real progress service; habit actions never create tasks.
    db.process_update(50, action or (lambda: db.record_course_reply(81, 90, text, local_date=NOW.date())))
    current = habits.preference(db, 'competitive-programming')
    if expected == 'set':
        assert current == [first, second]
    elif expected == 'disable':
        assert current == []
    else:
        assert current == previous
    assert not db.list_open_tasks()
    payload = json.loads(model._generate_output.call_args.args[1])
    assert payload['reply'] == text
    assert payload['saved_items'] == previous


def test_unknown_course_and_ordinary_progress_never_call_ai(db):
    start(db)
    ai = AsyncMock()
    assert asyncio.run(habits.prepare_reply(db, ai, 999, '以後每週都做兩題')) is None
    assert asyncio.run(habits.prepare_reply(db, ai, 81, '今天上到動態規劃')) is None
    ai._generate_output.assert_not_awaited()


def test_missing_reference_clarifies_without_model_or_progress_write(db):
    from chronos.course_tracking import new_session, COURSE_SCHEDULE
    session = new_session(COURSE_SCHEDULE[4], NOW.date(), 91)
    db.create_course_session(session)
    ai = AsyncMock()
    action = asyncio.run(habits.prepare_reply(db, ai, 91, '以後都照今天這樣做。'))
    receipt = db.process_update(70, action)
    assert receipt['reply'] == habits.CLARIFY_TEXT
    ai._generate_output.assert_not_awaited()
    assert db.get_course_session_by_prompt(91) == session
    assert habits.preference(db, session.course_key) == []


@pytest.mark.parametrize('error', [AIError('unavailable'), ValueError('bad schema')])
def test_failure_preserves_preference_and_progress(db, error):
    start(db)
    before = db.get_course_session_by_prompt(81)
    ai = AsyncMock()
    ai._generate_output.side_effect = error
    action = asyncio.run(habits.prepare_reply(db, ai, 81, '以後第二項改成十題'))
    receipt = db.process_update(60, action)
    assert '沒有變更' in receipt['reply']
    assert habits.preference(db, before.course_key) == habits.DEFAULTS[before.course_key]
    assert db.get_course_session_by_prompt(81) == before


def test_preference_race_does_not_overwrite_new_value(db):
    start(db)
    ai = AsyncMock()
    ai._generate_output.return_value = result('set', 'A', 'B')
    action = asyncio.run(habits.prepare_reply(db, ai, 81, '以後固定做 A 和 B'))
    db.process_update(61, lambda: habits.command(db, '/coursehabit competitive-programming | New A | New B'))
    receipt = db.process_update(62, action)
    assert '沒有覆蓋' in receipt['reply']
    assert habits.preference(db, 'competitive-programming') == ['New A', 'New B']


@pytest.mark.parametrize('payload', [
    {'action': 'set', 'first': None, 'second': 'B'},
    {'action': 'progress', 'first': 'A', 'second': 'B'},
    {'action': 'accept', 'first': None, 'second': None},
])
def test_bounded_intent_schema_rejects_invalid_actions(payload):
    with pytest.raises(ValidationError):
        habits.HabitIntent.model_validate(payload)


def test_real_provider_transport_uses_habit_schema(db, monkeypatch):
    start(db)
    output = {'action': 'set', 'first': 'Review examples', 'second': 'Solve six problems'}
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(output)}]}}]})
    mock_provider(monkeypatch, handler)
    action = asyncio.run(habits.prepare_reply(db, ExternalAI(config()), 81,
        'After every class review examples and solve six problems'))
    db.process_update(63, action)
    assert habits.preference(db, 'competitive-programming') == ['Review examples', 'Solve six problems']
    assert requests


def test_webhook_semantic_reply_does_not_record_a_habit_as_progress(system, monkeypatch):
    client, tasks, bot, cfg = system
    start(main.db)
    model = AsyncMock(return_value=result('set', 'Review examples', 'Solve ten problems'))
    monkeypatch.setattr(main.ai, '_generate_output', model)
    payload = {'update_id': 65, 'message': {'message_id': 90, 'chat': {'id': 123},
        'text': 'After every class review examples and solve ten problems',
        'reply_to_message': {'message_id': 81}}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    before = main.db.get_course_session_by_prompt(81)
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    model.assert_awaited_once()
    assert not tasks.list_open()
    assert main.db.get_course_session_by_prompt(81) == before
    assert habits.preference(main.db, 'competitive-programming') == ['Review examples', 'Solve ten problems']

import asyncio
import copy
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from chronos import main, intents
from chronos.google_calendar import CalendarError
from chronos.intents import Intent
from test_intents import store

NOW = datetime.fromisoformat('2026-10-24T18:27:00+08:00')
TEXT = '九點過後是否有 atcoder competition？'
SAMPLE = Path(__file__).parent / 'fixtures' / 'named_agenda_query.json'


def event(title, begin, end):
    return {'summary': title, 'start': {'dateTime': f'2026-10-24T{begin}:00+08:00'},
            'end': {'dateTime': f'2026-10-24T{end}:00+08:00'}}


@pytest.mark.parametrize('matching', [False, True])
def test_named_query_keeps_time_and_event_name(store, monkeypatch, matching):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(intents, 'datetime', Clock)
    record = json.loads(SAMPLE.read_text(encoding='utf-8'))
    classifier = AsyncMock(return_value=Intent.model_validate(record['intent']))
    monkeypatch.setattr(main.ai, 'classify_intent', classifier)
    calendar = AsyncMock()
    calendar.agenda.return_value = [event('Codeforces Round 1129', '19:05', '21:05')]
    if matching:
        calendar.agenda.return_value.append(event('AtCoder Beginner Contest 999', '21:30', '23:10'))
    monkeypatch.setattr(main, 'google_calendar', calendar)
    action = asyncio.run(main.prepare_message(TEXT, update_id=90100, chat_id=123))
    result = store.process_update(90100, action)
    reply = result['reply']
    begin, end = calendar.agenda.await_args.args
    assert begin.isoformat() == '2026-10-24T21:00:00+08:00'
    assert end.isoformat() == '2026-10-25T00:00:00+08:00'
    assert 'Codeforces' not in reply, reply
    if matching:
        assert '21:30–23:10 · AtCoder Beginner Contest 999' in reply
        assert 'minutes' not in reply
    else:
        assert 'AtCoder'.casefold() in reply.casefold()
        assert '沒有相符' in reply
    calendar.verify_owner.assert_awaited_once()
    calendar.insert.assert_not_awaited()
    main.ai.parse.assert_not_awaited()
    assert main.tasks.list_open() == []


@pytest.mark.parametrize('keywords,expected', [
    (['atcoder'], ['AtＣoder Beginner Contest', 'AT CODER Regular Contest']),
    (['AtCoder', 'Beginner'], ['AtＣoder Beginner Contest']),
    ([], ['AtＣoder Beginner Contest', 'AT CODER Regular Contest', 'Codeforces Round 1129']),
    (['!!!'], []),
])
def test_literal_name_filter(keywords, expected):
    from chronos.agenda_query import matching_events
    events = [{'summary': title, 'description': 'AtCoder Beginner Contest'} for title in
              ['AtＣoder Beginner Contest', 'AT CODER Regular Contest', 'Codeforces Round 1129']]
    assert [item['summary'] for item in matching_events(events, keywords)] == expected


def test_missing_result_escapes_name():
    from chronos.agenda_query import empty_named_query
    text = empty_named_query(['<AtCoder>'], NOW, NOW)
    assert '&lt;AtCoder&gt;' in text and '<AtCoder>' not in text


@pytest.mark.parametrize('failure', ['unrelated-name', 'calendar-unavailable'])
def test_name_and_provider_failures_do_not_report_false_results(store, monkeypatch, failure):
    record = json.loads(SAMPLE.read_text(encoding='utf-8'))
    raw = copy.deepcopy(record['intent'])
    if failure == 'unrelated-name':
        raw['agenda_keywords'] = ['Codeforces']
    monkeypatch.setattr(main.ai, 'classify_intent', AsyncMock(return_value=Intent.model_validate(raw)))
    calendar = AsyncMock()
    calendar.verify_owner.side_effect = CalendarError('calendar unavailable')
    monkeypatch.setattr(main, 'google_calendar', calendar)
    result = store.process_update(90101, asyncio.run(main.prepare_message(TEXT, update_id=90101, chat_id=123)))
    assert '沒有相符' not in result['reply']
    assert main.tasks.list_open() == []
    calendar.agenda.assert_not_awaited()
    if failure == 'unrelated-name':
        calendar.verify_owner.assert_not_awaited()
    else:
        calendar.verify_owner.assert_awaited_once()

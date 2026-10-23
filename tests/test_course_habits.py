import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import course_habits as habits, main
from chronos.course_tracking import TAIPEI, COURSE_SCHEDULE, new_session
from chronos.db import Database
from chronos.study_scheduler import tick_study
from test_firestore import database
from test_system import system

NOW = datetime(2026, 10, 22, 21, 15, tzinfo=TAIPEI)


@pytest.fixture(params=['sqlite', 'firestore'])
def db(request, tmp_path, monkeypatch):
    if request.param == 'firestore':
        return database(monkeypatch)
    result = Database(tmp_path / 'habits.db')
    result.initialize()
    return result


def start(db):
    db.create_course_session(new_session(COURSE_SCHEDULE[4], NOW.date(), 80))
    bot = AsyncMock()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 81}}
    asyncio.run(tick_study(db, bot, 123, NOW))
    return bot


def click(db, index, action='accept', update=1, chat=123, message=81, now=NOW):
    session = db.get_course_session_by_prompt(81)
    data = f'chabit:{habits.token_for(session)}:{index}:{action}'
    return db.process_update(update, lambda: habits.respond(db, data, chat, message, now))


def test_after_class_individual_approval_and_replay(db):
    bot = start(db)
    assert not db.list_open_tasks()
    assert '六題' in bot.send_message.call_args.args[1]
    assert len(bot.send_message.call_args.kwargs['reply_markup']['inline_keyboard']) == 2
    asyncio.run(tick_study(db, bot, 123, NOW + timedelta(hours=2)))
    assert bot.send_message.await_count == 1
    first = click(db, 0)
    assert len(db.list_open_tasks()) == 1
    assert click(db, 0) == first
    click(db, 0, update=2)
    assert len(db.list_open_tasks()) == 1
    click(db, 1, 'skip', update=3)
    assert len(db.list_open_tasks()) == 1
    click(db, 1, update=4)
    assert len(db.list_open_tasks()) == 1
    assert '2026-10-22' in db.list_open_tasks()[0]['title']
    assert db.list_open_tasks()[0]['due_at'] is None


def test_invalid_owner_message_and_expiry(db):
    start(db)
    click(db, 0, chat=999)
    click(db, 0, message=999, update=2)
    click(db, 0, now=NOW + timedelta(days=8), update=3)
    assert not db.list_open_tasks()
    click(db, 0, update=4)
    click(db, 1, update=5)
    assert len(db.list_open_tasks()) == 2


def test_preferences_explicit_course_scoped_and_disable(db, monkeypatch):
    start(db)
    monkeypatch.setattr(main, 'db', db)
    result = db.process_update(20, lambda: main.record_course_progress_reply(
        81, 90, '通常都是 1. 複習例題 2. 完成十題', local_date=NOW.date()))
    assert '記住' in result['reply']
    assert habits.preference(db, 'competitive-programming') == ['複習例題', '完成十題']
    assert habits.preference(db, 'database-systems') == []
    assert habits.parse_items('今天上到 1. DP 2. graph') is None
    assert not db.list_open_tasks()
    action = asyncio.run(main.prepare_message('/coursehabit competitive-programming | off'))
    db.process_update(21, action)
    assert habits.preference(db, 'competitive-programming') == []
    # Already-issued cards retain exactly the content the owner saw.
    click(db, 0, update=22)
    assert '學習本週上課內容' in db.list_open_tasks()[0]['title']


def test_next_week_uses_saved_items_without_repeated_prompt(db):
    db.process_update(1, lambda: habits.command(db, '/coursehabit competitive-programming | Review examples | Solve ten problems'))
    bot = start(db)
    assert 'Solve ten problems' in bot.send_message.call_args.args[1]
    assert not db.list_open_tasks()


def test_approval_and_receipt_roll_back_together(db, monkeypatch):
    start(db)
    original = db._save_task_action_state
    def fail(key, state):
        if key.startswith('course-proposal:'):
            raise RuntimeError('simulated persistence failure')
        return original(key, state)
    monkeypatch.setattr(db, '_save_task_action_state', fail)
    with pytest.raises(RuntimeError):
        click(db, 0)
    assert not db.list_open_tasks()
    assert db.get_update(1) is None
    monkeypatch.setattr(db, '_save_task_action_state', original)
    click(db, 0)
    assert len(db.list_open_tasks()) == 1


def test_webhook_accepts_only_after_click_and_replays_receipt(system):
    client, tasks, bot, cfg = system
    db = main.db
    start(db)
    session = db.get_course_session_by_prompt(81)
    token = habits.token_for(session)
    payload = {'update_id': 71, 'callback_query': {'id': 'accept',
        'data': f'chabit:{token}:0:accept',
        'message': {'message_id': 81, 'chat': {'id': 123}, 'text': 'proposal'}}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert not tasks.list_open()
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert len(tasks.list_open()) == 1
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert len(tasks.list_open()) == 1
    assert bot.send_message.await_count == 1

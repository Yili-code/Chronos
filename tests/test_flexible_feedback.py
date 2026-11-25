"""Partial feedback works for non-reading tasks and optional measurements."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import main
from chronos.learning_calendar import LearningCalendar
from chronos.study_feedback import PROMPT, extract, progress_note, skip_details
from chronos.task_actions import task_fields
from test_learning_calendar import setup
from test_system import system
from test_intents import store


def test_opening_is_neutral_and_has_no_examples():
    assert PROMPT == '這次大概投入多久？完成多少進度了？'


@pytest.mark.parametrize('text', ['懶得想', '懶得想了。', '不知道', '沒記時間', '先跳過', '10min，懶得想'])
def test_explicit_opt_out(text):
    assert skip_details(text)


@pytest.mark.parametrize('text', ['懶得想要不要改期', '明天安排10min', '完成登入頁', '10min'])
def test_opt_out_does_not_match_other_requests(text):
    assert not skip_details(text)


@pytest.mark.parametrize('text', ['做到第3個步驟', '看到第2章', '完成登入頁，還剩測試'])
def test_non_page_progress_does_not_invent_pages(text):
    assert 'completed' not in extract(text)
    assert progress_note(text)


def pending(planner, task, **extra):
    planner.update(task['id'], lambda _: {'token': 'flexible', 'status': 'awaiting_feedback',
        'snapshot': task_fields(task), **extra})


def test_time_only_asks_neutral_question_and_keeps_draft(setup):
    planner, task, calendar, _ = setup
    pending(planner, task)
    reply = planner.partial_reply(task['id'], 'flexible', '10min')
    assert '10 分鐘' in reply and '做到哪裡' in reply and '頁' not in reply
    assert planner.get(task['id'])['partial_draft'] == {'minutes': 10}
    assert planner.db.get_task(task['id'])['status'] == 'open'
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('timed', [False, True])
def test_opt_out_finishes_feedback_and_preserves_only_reported_effort(setup, timed):
    planner, task, calendar, bot = setup
    pending(planner, task)
    if timed:
        planner.partial_reply(task['id'], 'flexible', '10min')
    planner = LearningCalendar(planner.db, calendar, bot, planner.settings)
    reply = planner.partial_reply(task['id'], 'flexible', '懶得想')
    state = planner.get(task['id'])
    assert '先不細填' in reply and '？' not in reply and 'None' not in reply
    assert state['feedback']['outcome'] == 'partial'
    assert state['feedback']['actual_minutes'] == (10 if timed else None)
    assert state['status'] == 'finished' and 'partial_draft' not in state
    successor = planner.get(state['successor_task_id'])
    assert successor['status'] == 'queued'
    assert 'progress' not in successor and 'requested_minutes' not in successor
    assert planner.db.get_task(state['successor_task_id'])['status'] == 'open'
    assert not planner.db.get_task_action_state('learning:profile')
    count = len(planner.db.list_open_tasks())
    planner.partial_reply(task['id'], 'flexible', '懶得想')
    assert len(planner.db.list_open_tasks()) == count
    calendar.insert.assert_not_awaited()
    calendar.patch.assert_not_awaited()


@pytest.mark.parametrize('description_first', [False, True])
def test_descriptive_progress_survives_multiple_turns(setup, description_first):
    planner, task, calendar, bot = setup
    pending(planner, task)
    description = '完成登入頁，還剩測試'
    first, second = (description, '10min') if description_first else ('10min', description)
    planner.partial_reply(task['id'], 'flexible', first)
    planner = LearningCalendar(planner.db, calendar, bot, planner.settings)
    reply = planner.partial_reply(task['id'], 'flexible', second)
    state = planner.get(task['id'])
    assert state['feedback']['actual_minutes'] == 10
    assert state['feedback']['reason'] == description
    assert description in reply and '頁' not in reply.replace('登入頁', '')
    successor = planner.get(state['successor_task_id'])
    assert successor['progress_note'] == description and 'progress' not in successor
    assert 'requested_minutes' not in successor
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('report', ['到第20頁，總共48頁', '完成4/7題'])
def test_progress_is_kept_when_owner_declines_duration(setup, report):
    planner, task, calendar, _ = setup
    pending(planner, task)
    planner.partial_reply(task['id'], 'flexible', report)
    reply = planner.partial_reply(task['id'], 'flexible', '沒記時間')
    state = planner.get(task['id'])
    assert state['feedback']['actual_minutes'] is None and 'None' not in reply
    assert state['progress'].get('completed_pages', state['progress'].get('completed_questions')) in {20, 4}
    assert 'requested_minutes' not in planner.get(state['successor_task_id'])
    calendar.insert.assert_not_awaited()


def test_opt_out_cannot_bypass_stale_token(setup):
    planner, task, _, _ = setup
    pending(planner, task)
    reply = planner.partial_reply(task['id'], 'old-token', '懶得想')
    assert '已經結束' in reply
    assert planner.get(task['id'])['status'] == 'awaiting_feedback'
    assert len(planner.db.list_open_tasks()) == 1


def test_effort_and_opt_out_in_one_reply(setup):
    planner, task, _, _ = setup
    pending(planner, task)
    reply = planner.partial_reply(task['id'], 'flexible', '10min，懶得想')
    assert '10 分鐘' in reply and '先不細填' in reply
    assert planner.get(task['id'])['feedback']['actual_minutes'] == 10


def test_webhook_opt_out_closes_context_once_without_model_or_calendar_writes(system, store, monkeypatch):
    client, _, bot, config = system
    config.enable_learning_calendar = True
    monkeypatch.setattr(LearningCalendar, 'tick', AsyncMock(return_value={}))
    calendar = AsyncMock()
    monkeypatch.setattr(main, 'google_calendar', calendar)
    classifier = AsyncMock(side_effect=AssertionError('Feedback must not invoke intent classification'))
    monkeypatch.setattr(main.ai, 'classify_intent', classifier)
    task = main.tasks.create('實作登入功能')
    planner = main.learning_calendar()
    pending(planner, task, partial_draft={'minutes': 10})
    main.db.mutate_task_action_state('learning:edit-chat:123', lambda _: {
        'task_id': task['id'], 'token': 'flexible', 'mode': 'feedback',
        'expires_at': (datetime.now(config.tz) + timedelta(hours=1)).isoformat()})
    payload = {'update_id': 89125, 'message': {'message_id': 89125, 'chat': {'id': 123}, 'text': '懶得想'}}
    for _ in range(2):
        assert client.post('/telegram/webhook', json=payload,
            headers={'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}).status_code == 200
    assert bot.send_message.await_count == 1
    assert not main.db.get_task_action_state('learning:edit-chat:123')
    assert planner.get(task['id'])['feedback']['actual_minutes'] == 10
    classifier.assert_not_awaited()
    calendar.insert.assert_not_awaited()
    calendar.patch.assert_not_awaited()

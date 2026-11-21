"""Contradictory date evidence cannot authorize a schedule or overwrite a correction."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chronos.temporal_constraints import literal_weekday_conflict
from test_learning_calendar import setup, NOW
from test_booked_edit_reliability import booked
from test_deadline_reminders import store
from test_backward_planning import setup as backward_setup, output, preview
from chronos.backward_planning import key

SUNDAY = datetime.fromisoformat('2026-10-25T10:00:00+08:00')

@pytest.mark.parametrize('text', ['明天（星期二）14:00', 'tomorrow (Tuesday) at 14:00',
    '2026-10-27 Monday 14:00', '10/27（星期一）', '後天星期一', '2026-02-30 星期一'])
def test_inconsistent_literal_assertions_ask_instead_of_choosing(text):
    assert literal_weekday_conflict(text, SUNDAY)

@pytest.mark.parametrize('text', ['明天（星期一）14:00', 'tomorrow (Monday) at 14:00',
    '2026-10-27 Tuesday 14:00', '10/27（星期二）', '明天讀書，星期二考試',
    '把「明天星期二」的標題換掉', '明天下午讀星期二的教材'])
def test_compatible_or_separate_roles_are_not_flagged(text):
    assert literal_weekday_conflict(text, SUNDAY) is None


def test_conflicting_booked_edit_does_not_parse_or_write(setup):
    planner, task, calendar, event, original = booked(setup)
    planner.ai = SimpleNamespace(parse_schedule_edit=AsyncMock(return_value=SimpleNamespace(
        actionable=True, timing='exact', start='2026-10-21T14:30:00+08:00', minutes=30, progress_note='')))
    reply = asyncio.run(planner.revise(task['id'], 'booked', '明天（星期三）14:30，半小時', NOW))
    assert '不一致' in reply
    assert planner.get(task['id']) == original
    planner.ai.parse_schedule_edit.assert_not_awaited()
    calendar.move_event.assert_not_awaited()


def test_backward_conflicting_date_asks_without_calendar_read(store):
    planner, calendar, ai, events = backward_setup(store)
    token, reply = preview(planner, '2026-10-30（星期四）18:00 提交報告')
    state = store.get_task_action_state(key(token))
    assert state['status'] == 'clarifying'
    assert '不一致' in state['question']
    calendar.busy.assert_not_awaited()
    calendar.insert.assert_not_awaited()
    assert not events and not store.list_open_tasks()


def test_backward_new_turn_date_correction_overrides_old_relative_date(store):
    planner, calendar, ai, events = backward_setup(store, output(deadline=None, question='Which day?'))
    first, _ = preview(planner, '明天（星期三）交報告')
    assert store.get_task_action_state(key(first))['status'] == 'clarifying'
    ai.backward_plan.return_value = output(deadline='2026-10-21', question=None)
    second, _ = preview(planner, '依星期三', 2)
    saved = store.get_task_action_state(key(second))
    assert saved['deadline'] == '2026-10-21'
    assert saved['status'] == 'draft'
    assert store.get_task_action_state(key(first))['status'] == 'superseded'
    assert not events and not store.list_open_tasks()

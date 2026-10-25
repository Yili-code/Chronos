"""Explicit time qualifiers must survive the deterministic deadline shortcut."""
from datetime import datetime

import pytest

from chronos.backward_planning import explicit_deadline, key
from test_backward_planning import NOW, setup, output, preview
from test_deadline_reminders import store


@pytest.mark.parametrize('text', [
    '明天 5:00 PM 提交報告', '明天 5:00 p.m. 提交報告',
    '明天 17:00 UTC 提交報告', '明天 17:00 GMT 提交報告',
    '2026-10-20 17:00-04:00 提交報告',
    '2026-10-20 17:00 America/New_York 提交報告',
])
def test_qualified_clocks_are_not_overridden_as_taipei_24h(text):
    assert explicit_deadline(text, NOW) is None


@pytest.mark.parametrize('text,deadline', [
    ('明天 5:00 PM 提交報告', '2026-10-20T17:00:00+08:00'),
    ('明天 17:00 UTC 提交報告', '2026-10-21T01:00:00+08:00'),
])
def test_backward_preview_preserves_provider_resolved_time_qualifier(store, text, deadline):
    planner, calendar, _, events = setup(store, output(deadline=deadline))
    token, reply = preview(planner, text)
    saved = store.get_task_action_state(key(token))
    assert datetime.fromisoformat(saved['deadline']) == datetime.fromisoformat(deadline)
    assert all(datetime.fromisoformat(s['end']) <= datetime.fromisoformat(deadline) for s in saved['slots'])
    assert not store.list_open_tasks() and not events
    calendar.insert.assert_not_awaited()

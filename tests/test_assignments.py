from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from chronos.assignments import Assignment, complete, deadline_question, due_reminder, set_deadline
from chronos.course_tracking import TAIPEI


NOW = datetime(2026, 10, 5, 12, tzinfo=TAIPEI)


def assignment(**changes):
    return replace(Assignment("course-1", "assignment-1", "Report", "Instructions", NOW), **changes)


def test_missing_deadline_is_pending_not_guessed():
    item = assignment()
    assert item.status == "deadline_pending"
    assert due_reminder(item, NOW, sent_keys=set()) is None
    assert "尚未提供" in deadline_question(item)


def test_identity_survives_title_and_deadline_changes():
    original = assignment()
    assert replace(original, title="Updated").key == original.key
    assert set_deadline(original, NOW + timedelta(days=8), origin="owner").key == original.key
    assert assignment(course_id="another").key != original.key
    assert assignment(source_id="another").key != original.key


@pytest.mark.parametrize("label,hours", [("7d", 168), ("3d", 72), ("1d", 24), ("3h", 3)])
def test_exact_reminder_thresholds_and_dedup(label, hours):
    item = set_deadline(assignment(), NOW + timedelta(hours=hours), origin="source")
    key = due_reminder(item, NOW, sent_keys=set())
    assert key.endswith(":" + label)
    assert due_reminder(item, NOW, sent_keys={key}) is None


def test_late_discovery_does_not_emit_old_thresholds():
    item = set_deadline(assignment(), NOW + timedelta(hours=12), origin="owner")
    key = due_reminder(item, NOW, sent_keys=set())
    assert key.endswith(":1d")
    assert due_reminder(item, NOW, sent_keys={key}) is None


def test_completion_preserves_history_and_stops_reminders():
    item = set_deadline(assignment(), NOW + timedelta(hours=2), origin="source")
    done = complete(item, NOW)
    assert done.status == "done" and done.description == item.description
    assert done.completed_at == NOW
    assert due_reminder(done, NOW, sent_keys=set()) is None
    assert complete(done, NOW + timedelta(hours=1)) == done
    with pytest.raises(ValueError):
        set_deadline(done, NOW, origin="owner")


def test_deadline_revision_invalidates_old_reminder_keys_only_on_change():
    item = set_deadline(assignment(), NOW + timedelta(hours=2), origin="source")
    key = due_reminder(item, NOW, sent_keys=set())
    same = set_deadline(item, item.deadline, origin="source")
    assert due_reminder(same, NOW, sent_keys={key}) is None
    changed = set_deadline(item, NOW + timedelta(hours=1), origin="owner")
    assert due_reminder(changed, NOW, sent_keys={key}) is not None


def test_expired_and_not_yet_discovered_are_not_reminded():
    item = set_deadline(assignment(), NOW, origin="source")
    assert due_reminder(item, NOW, sent_keys=set()) is None
    assert due_reminder(item, NOW - timedelta(hours=1), sent_keys=set()) is None


def test_reject_naive_dates_and_inferred_deadlines():
    with pytest.raises(ValueError):
        assignment(discovered_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        set_deadline(assignment(), NOW, origin="inferred")
    with pytest.raises(ValueError):
        set_deadline(assignment(), NOW.replace(tzinfo=None), origin="owner")

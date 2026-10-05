from dataclasses import replace
from datetime import datetime, timedelta
from chronos.assignments import Assignment, merge_source_observation, owner_deadline_edit, to_record, from_record
from chronos.course_tracking import TAIPEI


def test_owner_cleared_deadline_survives_source_refresh():
    now = datetime.now(TAIPEI)
    original = Assignment('c', 's', 'Old', 'Old description', now, now + timedelta(days=2), 'source')
    cleared = from_record(to_record(owner_deadline_edit(original, None)))
    incoming = replace(original, title='New', discovered_at=now + timedelta(hours=1))
    merged = merge_source_observation(cleared, incoming)
    assert merged.deadline is None
    assert merged.deadline_owner_override
    assert merged.title == 'New'
    assert merged.discovered_at == now


def test_source_revision_and_partial_attachments():
    now = datetime.now(TAIPEI)
    old = Assignment('c', 's', 'Lab', 'Instructions', now, now + timedelta(days=1), 'source',
                     attachments=(('1', 'first.pdf'),), submission_status='submitted')
    new = replace(old, deadline=now + timedelta(days=2), discovered_at=now + timedelta(hours=1),
                  attachments=(('2', 'second.pdf'),), submission_status='unknown')
    merged = merge_source_observation(old, new)
    assert merged.deadline_revision == 1
    assert len(merged.attachments) == 2
    assert merged.submission_status == 'submitted'
    assert merge_source_observation(merged, old) == merged
    assert merge_source_observation(replace(old, completed_at=now), new).completed_at == now

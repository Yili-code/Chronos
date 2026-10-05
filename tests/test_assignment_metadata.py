from datetime import datetime
import pytest
from chronos.assignments import Assignment, to_record, from_record
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_metadata_roundtrip_never_completes_task(backend, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path / 'metadata.db')
    db.initialize()
    item = Assignment('course', 'source', 'Lab', 'Instructions', datetime.now(TAIPEI),
                      submission_status='submitted', attachments=(('123', 'lab.pdf'),))
    stored = db.create_assignment(item)
    observed = db.get_assignment(item.key)['assignment']
    assert observed.attachments == (('123', 'lab.pdf'),)
    assert observed.submission_status == 'submitted'
    assert observed.completed_at is None
    assert db.list_open_tasks()[0]['id'] == stored['task_id']
    old = to_record(item)
    del old['submission_status']
    del old['attachments']
    assert from_record(old).submission_status == 'unknown'
    assert from_record(old).attachments == ()

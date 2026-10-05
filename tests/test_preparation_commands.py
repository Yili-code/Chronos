from datetime import datetime
import pytest
from chronos.assignments import Assignment
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.preparation_commands import prepare_action
from chronos.preparation_commands import preparation_key
from chronos.assignments import to_record
from dataclasses import replace
from datetime import timedelta
from test_firestore import database as firestore_fake


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_explicit_queue_dedup_and_receipt_transaction(backend, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path / 'prepare.db')
    db.initialize()
    now = datetime.now(TAIPEI)
    task = db.create_assignment(Assignment('course', 'source', 'Lab', 'Implement sort', now))
    assert db.list_preparations() == []
    action = prepare_action(db, f"prepare {task['task_id']}", now)
    for update in (1, 1, 2):
        db.process_update(update, action)
    assert len(db.list_preparations()) == 1
    state = db.list_preparations()[0][1]
    assert state['status'] == 'queued'
    assert state['assignment']['description'] == 'Implement sort'
    assert state['draft'] is None
    assert '用法' in prepare_action(db, 'prepare', now)()
    assert '找不到' in prepare_action(db, 'prepare 999', now)()


def test_preparation_identity_tracks_only_actual_generation_input():
    now = datetime.now(TAIPEI)
    item = Assignment('course', 'source', 'Lab', 'Implement sort', now)
    key = preparation_key(to_record(item))
    metadata = replace(item, source_observed_at=now + timedelta(hours=1),
                       source_revision=3, submission_status='submitted',
                       attachments=(('1', 'instructions.pdf'),),
                       deadline=now + timedelta(days=1), deadline_origin='owner',
                       deadline_owner_override=True, deadline_revision=2)
    assert preparation_key(to_record(metadata)) == key
    for field in ('course_id', 'source_id', 'title', 'description'):
        assert preparation_key(to_record(replace(item, **{field: 'changed'}))) != key


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_legacy_uncertain_job_is_not_regenerated(backend, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path / 'legacy.db')
    db.initialize()
    now = datetime.now(TAIPEI)
    item = Assignment('course', 'source', 'Lab', 'Implement sort', now)
    task = db.create_assignment(item)
    old_source = to_record(replace(item, discovered_at=now - timedelta(days=1)))
    db.mutate_preparation('legacy-key', lambda _: {
        'status': 'uncertain', 'assignment': old_source, 'task_id': task['task_id'],
        'requested_at': now.isoformat(), 'version': 'prepare-v1', 'draft': None})
    reply = prepare_action(db, f"prepare {task['task_id']}", now)()
    assert 'uncertain' in reply
    assert len(db.list_preparations()) == 1

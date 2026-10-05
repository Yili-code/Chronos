from copy import deepcopy
from datetime import timedelta
import pytest
from chronos.assignment_bridge import AssignmentObservationStore
from chronos.assignment_sync import sync_assignment_observations
from chronos.db import Database
from test_assignment_bridge import PAYLOAD, NOW
from test_firestore import database as firestore_fake


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_import_is_idempotent_and_preserves_owner_edits(backend, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path / 'tasks.db')
    db.initialize()
    store = AssignmentObservationStore(tmp_path / 'observations.db')
    payload = deepcopy(PAYLOAD)
    payload['assignment']['course_id'] = '188571'
    store.put(payload, NOW)
    assert sync_assignment_observations(db, store, NOW)['created'] == 1
    task = db.list_open_tasks()[0]
    db.edit_task(task['id'], 'Owner title', NOW + timedelta(days=2), 'Owner')
    assert sync_assignment_observations(db, store, NOW)['existing'] == 1
    assert db.list_open_tasks()[0]['title'] == 'Owner title'
    db.clear_tasks()
    sync_assignment_observations(db, store, NOW)
    assert db.list_open_tasks() == []


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_new_source_deadline_updates_task_atomically(backend, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path / 'updates.db')
    db.initialize()
    store = AssignmentObservationStore(tmp_path / 'observed.db')
    payload = deepcopy(PAYLOAD)
    payload['assignment']['course_id'] = '188571'
    store.put(payload, NOW)
    sync_assignment_observations(db, store, NOW)
    task = db.list_open_tasks()[0]
    payload['assignment']['deadline'] = '2026-10-09T23:59:00+08:00'
    store.put(payload, NOW + timedelta(minutes=1))
    assert sync_assignment_observations(db, store, NOW + timedelta(minutes=1))['updated'] == 1
    item = db.get_assignment(db.list_assignment_keys()[0])['assignment']
    assert item.deadline_revision == 1
    assert db.list_open_tasks()[0]['due_at'] == payload['assignment']['deadline']
    db.edit_task(task['id'], 'Owner title', None, 'Owner')
    payload['assignment']['title'] = 'Source title'
    store.put(payload, NOW + timedelta(minutes=2))
    sync_assignment_observations(db, store, NOW + timedelta(minutes=2))
    assert db.list_open_tasks()[0]['title'] == 'Owner title'
    assert db.list_open_tasks()[0]['due_at'] is None
    db.complete_task(task['id'], NOW)
    store.put(payload, NOW + timedelta(minutes=3))
    assert sync_assignment_observations(db, store, NOW + timedelta(minutes=3))['updated'] == 0


def test_stale_observations_do_not_create_tasks(tmp_path):
    db = Database(tmp_path / 'tasks.db')
    db.initialize()
    store = AssignmentObservationStore(tmp_path / 'observations.db')
    payload = deepcopy(PAYLOAD)
    payload['assignment']['course_id'] = '188571'
    store.put(payload, NOW)
    assert sync_assignment_observations(db, store, NOW + timedelta(days=2))['stale'] == 1
    assert db.list_open_tasks() == []

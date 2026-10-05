from datetime import datetime
import pytest
from chronos.assignments import Assignment
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.preparation_commands import prepare_action
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

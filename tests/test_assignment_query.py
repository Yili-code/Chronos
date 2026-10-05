from datetime import datetime
import pytest
from chronos.assignment_commands import assignment_query, assignment_text
from chronos.assignments import Assignment, to_record
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_full_text_query_is_lossless_read_only_and_keeps_history(backend, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path / 'query.db')
    db.initialize()
    now = datetime.now(TAIPEI)
    item = Assignment('c', 's', 'Lab', '😀要求' * 1500, now, attachments=(('123', 'lab.pdf'),))
    task = db.create_assignment(item)
    before = to_record(db.get_assignment(item.key)['assignment'])
    expected = assignment_text(db.get_assignment(item.key))
    rebuilt = ''
    for page in range(1, (len(expected) + 1499) // 1500 + 1):
        text = assignment_query(db, f"assignment {task['task_id']} {page}")
        rebuilt += text.rsplit('\n\n第 ', 1)[0]
        assert len(text.encode('utf-16-le')) // 2 < 4096
    assert rebuilt == expected
    assert to_record(db.get_assignment(item.key)['assignment']) == before
    db.complete_task(task['task_id'], now)
    assert 'Lab' in assignment_query(db, f"assignment {task['task_id']}")
    db.clear_tasks()
    assert 'Lab' in assignment_query(db, f"assignment {task['task_id']}")

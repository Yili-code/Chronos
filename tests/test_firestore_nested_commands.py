from unittest.mock import Mock

from chronos.course_day_commands import classday_action
from chronos.exams import exam_action
from test_firestore import database


def test_owner_commands_share_webhook_transaction(monkeypatch):
    db = database(monkeypatch)
    factory = Mock(wraps=db.client.transaction)
    monkeypatch.setattr(db.client, 'transaction', factory)
    db.process_update(991, exam_action(db, 'exam OS | Midterm | ? | ? | ? | ?'))
    assert factory.call_count == 1
    db.process_update(992, classday_action(db, 'classday 2026-10-05 security off'))
    assert factory.call_count == 2
    assert db.get_course_day_decision('security:2026-10-05') == 'off'

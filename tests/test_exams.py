import pytest

from chronos.db import Database
from chronos.exams import exam_action, format_exams
from test_firestore import database as firestore_fake


@pytest.fixture(params=['sqlite', 'firestore'])
def db(request, tmp_path, monkeypatch):
    value = firestore_fake(monkeypatch) if request.param == 'firestore' else Database(tmp_path / 'exam.db')
    value.initialize()
    return value


def test_explicit_unknown_fields_and_update(db):
    exam_action(db, 'exam OS | 2026期中 | ? | ? | Ch1 | ?')()
    record = db.list_exams()[0]
    assert record['starts_at'] is None
    assert record['location'] is None
    assert '尚未提供' in format_exams(db.list_exams())
    exam_action(db, 'exam OS | 2026期中 | 2026-10-26 09:00 | Room A | Ch1–3 | Lab1')()
    assert len(db.list_exams()) == 1
    assert db.list_exams()[0]['starts_at'] == '2026-10-26T09:00:00+08:00'


@pytest.mark.parametrize('command', ['exam', 'exam OS | midterm | tomorrow | ? | ? | ?',
    'exam OS | midterm | 2026-02-30 09:00 | ? | ? | ?', 'exam ? | ? | ? | ? | ? | ?'])
def test_invalid_input_does_not_write(db, command):
    assert '用法' in exam_action(db, command)()
    assert db.list_exams() == []


def test_exam_save_inside_telegram_receipt_transaction(db):
    action = exam_action(db, 'exam OS | 2026期中 | ? | ? | ? | ?')
    db.process_update(99001, action)
    db.process_update(99001, action)
    assert len(db.list_exams()) == 1

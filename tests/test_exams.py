import asyncio
import pytest

from chronos import main
from chronos.db import Database
from chronos.exams import exam_action, format_exams, exam_pages, exams_query, exam_key
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


def test_exam_commands_are_no_longer_owner_facing(db, monkeypatch):
    monkeypatch.setattr(main, 'db', db)
    for command in ('/exam 作業系統期中考', '/exams', '/exams 2',
                    '/deadline 1 2026-10-20 12:00', '/assignment 1', '/assignment 1 2'):
        action = asyncio.run(main.prepare_message(command))
        assert action() == 'Unknown command. Use /help to see available commands.'
    assert db.list_exams() == []


def test_pagination_is_lossless_stable_and_unicode_bounded(db):
    for course in ('OS', 'DB', 'Architecture'):
        exam_action(db, f'exam {course} | Midterm | ? | ? | ' + '😀' * 1000 + ' | ' + '範圍' * 500)()
    records = db.list_exams()
    pages = exam_pages(records)
    assert len(pages) > 1
    assert ''.join(pages) == format_exams(sorted(records, key=exam_key))
    assert exam_pages(list(reversed(records))) == pages
    for index, content in enumerate(pages, 1):
        output = exams_query(db, f'exams {index}')
        assert output.startswith(content)
        assert len(output.encode('utf-16-le')) // 2 < 4096
    assert '下一頁：/exams 2' in exams_query(db, 'exams')
    assert '沒有第' in exams_query(db, 'exams 999')
    assert '用法' in exams_query(db, 'exams 0')


def test_notification_plan_first_writer_wins(db):
    original = [['a', 'Original'], ['b', 'Second part']]
    assert db.freeze_exam_notice_plan('exam:daily:2026-10-26', original) == original
    assert db.freeze_exam_notice_plan('exam:daily:2026-10-26', [['a', 'Changed']]) == original

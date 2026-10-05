"""The survey and actual studying have separate, durable completion states."""
import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest

from chronos.course_tracking import COURSE_SCHEDULE, TAIPEI, new_session
from chronos.db import Database
from chronos.study_scheduler import tick_study
from test_firestore import database as fake_firestore


@pytest.fixture(params=["sqlite", "firestore"])
def db(request, tmp_path, monkeypatch):
    if request.param == "firestore":
        return fake_firestore(monkeypatch)
    result = Database(tmp_path / "tasks.db")
    result.initialize()
    result.initialize()  # migration is repeatable
    return result


def test_scheduler_creates_one_survey_and_reply_creates_one_review(db):
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 501}}
    now = datetime(2026, 10, 5, 12, 10, tzinfo=TAIPEI)
    for _ in range(2):
        asyncio.run(tick_study(db, bot, 123, now))
    assert bot.send_message.await_count == 1
    survey, = db.list_open_tasks()
    assert survey["title"] == "填寫資訊安全實務與管理進度（2026-10-05）"
    action = lambda: db.record_course_reply(501, 502, "講義 A 第 10–12 頁", local_date=now.date())
    first = db.process_update(1, action)
    assert db.process_update(1, action) == first
    db.process_update(2, action)  # a different update replying to the same prompt
    review, = db.list_open_tasks()
    assert review["id"] != survey["id"]
    assert review["title"] == "複習資訊安全實務與管理（2026-10-05）：講義 A 第 10–12 頁"
    assert review["due_at"] is None
    assert f"/done {review['id']}" in first["reply"]
    assert db.complete_task(review["id"], now)
    assert not db.list_open_tasks()


def test_repeated_creation_and_cleared_survey_do_not_resurrect_tasks(db):
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 501)
    first = db.create_course_session(session, create_tasks=True)
    assert db.create_course_session(session, create_tasks=True) == first
    db.clear_tasks()
    db.create_course_session(session, create_tasks=True)
    assert not db.list_open_tasks()
    db.process_update(3, lambda: db.record_course_reply(501, 502, "第 3 章"))
    assert len(db.list_open_tasks()) == 1


def test_invalid_or_late_reply_does_not_create_review(db):
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 501)
    db.create_course_session(session, create_tasks=True)
    for update, prompt, text, day in [(1, 999, "chapter 1", date(2026, 10, 5)),
                                      (2, 501, "  ", date(2026, 10, 5)),
                                      (3, 501, "chapter 1", date(2026, 10, 6))]:
        db.process_update(update, lambda: db.record_course_reply(prompt, 502, text, local_date=day))
    survey, = db.list_open_tasks()
    assert survey["title"].startswith("填寫")


def test_old_sessions_do_not_backfill_tasks(db):
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 501)
    db.create_course_session(session)
    db.process_update(5, lambda: db.record_course_reply(501, 502, "Chapter 1"))
    assert not db.list_open_tasks()


def test_sqlite_reply_failure_rolls_back_tasks_session_and_receipt(tmp_path, monkeypatch):
    db = Database(tmp_path / "rollback.db")
    db.initialize()
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 501)
    stored = db.create_course_session(session, create_tasks=True)
    def fail(_):
        raise RuntimeError("simulated persistence failure")
    monkeypatch.setattr(db, "save_course_session", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        db.process_update(9, lambda: db.record_course_reply(501, 502, "Chapter 1"))
    survey, = db.list_open_tasks()
    assert survey["id"] == stored.survey_task_id
    assert db.get_course_session(session.session_id).status.value == "pending"
    assert db.get_update(9) is None

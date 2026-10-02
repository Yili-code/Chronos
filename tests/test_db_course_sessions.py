from datetime import date

from chronos.course_tracking import COURSE_SCHEDULE, ProgressStatus, new_session
from chronos.db import Database


def test_sqlite_course_session_lifecycle(tmp_path):
    db = Database(tmp_path / "chronos.db")
    db.initialize()
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    assert db.create_course_session(session) == session
    assert db.create_course_session(session) == session
    answered = session.__class__(**{**session.__dict__, "status": ProgressStatus.ANSWERED, "reported_progress": "第四章"})
    assert db.save_course_session(answered) == answered
    assert db.get_course_session(session.session_id) == answered


def test_late_reply_expires_session_with_update_receipt(tmp_path):
    db = Database(tmp_path / "chronos.db")
    db.initialize()
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    db.create_course_session(session)
    result = db.process_update(202, lambda: db.record_course_reply(
        101, 102, "Chapter 4", local_date=date(2026, 10, 6)))
    assert "期限已結束" in result["reply"]
    assert db.get_course_session(session.session_id).status is ProgressStatus.MISSED
    assert db.get_course_session(session.session_id).reported_progress is None
    assert db.get_update(202) is not None

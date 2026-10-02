from datetime import date, datetime, time, timedelta, timezone

from chronos.course_tracking import (
    COURSE_SCHEDULE,
    ProgressStatus,
    accept_reply,
    course_for_weekday,
    due_reminder,
    mark_missed_at_day_end,
    new_session,
    prompt_text,
    record_reminder,
)


def test_schedule_matches_phase_one_tracked_courses():
    assert len(COURSE_SCHEDULE) == 7
    assert [slot.name for slot in course_for_weekday(2)] == ["軟體工程", "圖論演算法"]
    assert course_for_weekday(5) == ()


def test_session_prompt_and_reply_require_telegram_correlation():
    slot = COURSE_SCHEDULE[2]
    session = new_session(slot, date(2026, 10, 7), prompt_message_id=101)
    assert "軟體工程剛下課" in prompt_text(slot.name)
    assert accept_reply(session, reply_to_message_id=999, reply_message_id=202, text="第四章") is None
    answered = accept_reply(session, reply_to_message_id=101, reply_message_id=202, text="  第四章  ")
    assert answered is not None
    assert answered.status is ProgressStatus.ANSWERED
    assert answered.reported_progress == "第四章"


def test_reminders_are_limited_to_two_and_stop_after_answer():
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), prompt_message_id=101)
    sent = datetime(2026, 10, 5, 12, 10, tzinfo=timezone.utc)
    assert due_reminder(session, now=sent + timedelta(minutes=59), prompt_sent_at=sent) is None
    assert due_reminder(session, now=sent + timedelta(hours=1), prompt_sent_at=sent) == 1
    session = record_reminder(session, 1)
    assert due_reminder(session, now=sent + timedelta(hours=2), prompt_sent_at=sent) == 2
    session = record_reminder(session, 2)
    assert due_reminder(session, now=sent + timedelta(hours=3), prompt_sent_at=sent) is None
    answered = accept_reply(session, reply_to_message_id=101, reply_message_id=303, text="第五頁")
    assert answered is not None
    assert answered.status is ProgressStatus.ANSWERED


def test_unanswered_session_becomes_missed_at_day_end_without_cross_day_reminder():
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), prompt_message_id=101)
    missed = mark_missed_at_day_end(session, local_date=date(2026, 10, 6))
    assert missed.status is ProgressStatus.MISSED
    assert mark_missed_at_day_end(session, local_date=date(2026, 10, 5)).status is ProgressStatus.PENDING
    assert mark_missed_at_day_end(session, local_date=date(2026, 10, 9)).status is ProgressStatus.MISSED


def test_reminders_stop_at_taipei_midnight_even_without_cleanup():
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    sent = datetime(2026, 10, 5, 4, 10, tzinfo=timezone.utc)
    midnight = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)
    assert due_reminder(session, now=midnight, prompt_sent_at=sent) is None


def test_cannot_reopen_answered_session_by_recording_reminder():
    import pytest
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    answered = accept_reply(session, reply_to_message_id=101, reply_message_id=102, text="Chapter 4")
    with pytest.raises(ValueError, match="terminal"):
        record_reminder(answered, 1)

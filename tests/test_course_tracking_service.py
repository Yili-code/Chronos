from datetime import date, datetime, timedelta, timezone

from chronos.course_tracking import COURSE_SCHEDULE, ProgressStatus
from chronos.course_tracking_service import CourseTrackingService
from chronos.course_tracking_store import InMemoryCourseSessionStore


class Messenger:
    def __init__(self):
        self.sent = []

    def send(self, text, *, reply_to_message_id=None):
        message_id = len(self.sent) + 100
        self.sent.append((message_id, text, reply_to_message_id))
        return message_id


def test_open_session_is_idempotent_and_sends_one_prompt():
    messenger = Messenger()
    service = CourseTrackingService(InMemoryCourseSessionStore(), messenger)
    first = service.open_session(COURSE_SCHEDULE[0], date(2026, 10, 5))
    second = service.open_session(COURSE_SCHEDULE[0], date(2026, 10, 5))
    assert first == second
    assert len(messenger.sent) == 1


def test_reply_and_reminder_share_the_original_prompt_message():
    messenger = Messenger()
    service = CourseTrackingService(InMemoryCourseSessionStore(), messenger)
    session = service.open_session(COURSE_SCHEDULE[0], date(2026, 10, 5))
    sent = datetime(2026, 10, 5, 12, 10, tzinfo=timezone.utc)
    reminded = service.send_due_reminder(session.session_id, now=sent + timedelta(hours=1), prompt_sent_at=sent)
    assert reminded.status is ProgressStatus.REMINDED_ONCE
    assert messenger.sent[-1][2] == session.prompt_message_id
    answered = service.handle_reply(
        session.session_id,
        reply_to_message_id=session.prompt_message_id,
        reply_message_id=303,
        text="第四章",
    )
    assert answered.status is ProgressStatus.ANSWERED

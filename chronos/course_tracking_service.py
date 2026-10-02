"""Application service for Phase 1 progress prompts and reminders."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from .course_tracking import (
    CourseSlot,
    ProgressSession,
    accept_reply,
    due_reminder,
    new_session,
    prompt_text,
    record_reminder,
)
from .course_tracking_store import CourseSessionStore


class ProgressMessenger(Protocol):
    def send(self, text: str, *, reply_to_message_id: int | None = None) -> int: ...


class CourseTrackingService:
    def __init__(self, store: CourseSessionStore, messenger: ProgressMessenger) -> None:
        self.store = store
        self.messenger = messenger

    def open_session(self, slot: CourseSlot, class_date: date) -> ProgressSession:
        """Create and deliver one prompt; retries do not send a duplicate."""
        candidate = new_session(slot, class_date, prompt_message_id=0)
        existing = self.store.get(candidate.session_id)
        if existing is not None:
            return existing
        prompt_message_id = self.messenger.send(prompt_text(slot.name))
        session = new_session(slot, class_date, prompt_message_id)
        return self.store.create(session)

    def handle_reply(self, session_id: str, *, reply_to_message_id: int, reply_message_id: int, text: str) -> ProgressSession | None:
        session = self.store.get(session_id)
        if session is None:
            return None
        answered = accept_reply(
            session,
            reply_to_message_id=reply_to_message_id,
            reply_message_id=reply_message_id,
            text=text,
        )
        if answered is None:
            return None
        return self.store.save(answered)

    def send_due_reminder(self, session_id: str, *, now: datetime, prompt_sent_at: datetime) -> ProgressSession | None:
        session = self.store.get(session_id)
        if session is None:
            return None
        number = due_reminder(session, now=now, prompt_sent_at=prompt_sent_at)
        if number is None:
            return session
        message_id = self.messenger.send(
            f"還沒收到{session.course_name}的課堂進度。請回覆最初的課後訊息。",
            reply_to_message_id=session.prompt_message_id,
        )
        if not message_id:
            raise RuntimeError("reminder delivery returned no message id")
        return self.store.save(record_reminder(session, number))

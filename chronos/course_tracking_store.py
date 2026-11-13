"""Persistence seam for Phase 1 course-progress sessions."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from time import time
from typing import Protocol

from .course_tracking import ProgressReportKind, ProgressSession, ProgressStatus, classify_progress
from .message_lifecycle import SECONDS


class CourseSessionStore(Protocol):
    def create(self, session: ProgressSession) -> ProgressSession: ...
    def get(self, session_id: str) -> ProgressSession | None: ...
    def save(self, session: ProgressSession) -> ProgressSession: ...


class InMemoryCourseSessionStore:
    """Deterministic store used by unit tests and local dry runs."""

    def __init__(self) -> None:
        self._sessions: dict[str, ProgressSession] = {}

    def create(self, session: ProgressSession) -> ProgressSession:
        if session.session_id in self._sessions:
            return self._sessions[session.session_id]
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> ProgressSession | None:
        return self._sessions.get(session_id)

    def save(self, session: ProgressSession) -> ProgressSession:
        if session.session_id not in self._sessions:
            raise KeyError(session.session_id)
        self._sessions[session.session_id] = session
        return session


def course_progress_receipt(session: ProgressSession, *, expire_prompt: bool) -> str | dict:
    course = f"{session.course_name}（{session.class_date:%m/%d}）"
    if session.report_kind is ProgressReportKind.UNKNOWN_SCOPE:
        text = f"已記錄 {course}：範圍尚待確認。\n填寫進度代辦已完成，已新增確認上課範圍代辦。"
    elif session.report_kind is ProgressReportKind.NO_PROGRESS:
        text = f"已記錄 {course}：今天無課或沒有進度。\n填寫進度代辦已完成，其他代辦保持不變。"
    else:
        text = f"已記錄 {course}的進度。\n填寫進度代辦已完成，其他代辦保持不變。"
    if not expire_prompt:
        return text
    return {"messages": [{"text": text, "course_card_expiry": {
        "message_id": session.prompt_message_id, "deadline": time() + SECONDS,
    }}]}


def session_to_firestore(session: ProgressSession) -> dict:
    """Return only non-secret, stable fields for a future Firestore adapter."""
    data = asdict(session)
    data["class_date"] = session.class_date.isoformat()
    data["status"] = session.status.value
    data["report_kind"] = session.report_kind.value if session.report_kind is not None else None
    return data


def session_from_firestore(data: dict) -> ProgressSession:
    reported_progress = data.get("reported_progress")
    raw_kind = data.get("report_kind")
    report_kind = (ProgressReportKind(str(raw_kind)) if raw_kind is not None else
                   classify_progress(str(reported_progress)) if reported_progress else None)
    return ProgressSession(
        session_id=str(data["session_id"]),
        course_key=str(data["course_key"]),
        course_name=str(data["course_name"]),
        class_date=date.fromisoformat(str(data["class_date"])),
        prompt_message_id=int(data["prompt_message_id"]),
        status=ProgressStatus(str(data["status"])),
        reminder_count=int(data["reminder_count"]),
        report_kind=report_kind,
        reported_progress=reported_progress,
        survey_task_id=data.get("survey_task_id"),
        reply_message_id=(int(data["reply_message_id"]) if data.get("reply_message_id") is not None else None),
    )

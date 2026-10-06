"""Pure Phase 1 course-tracking rules.

The module deliberately has no Telegram or Firestore dependency.  It models the
state transitions first so delivery and persistence adapters can be tested
independently.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from enum import Enum
import re
import unicodedata
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


class ProgressStatus(str, Enum):
    PENDING = "pending"
    REMINDED_ONCE = "reminded_once"
    REMINDED_TWICE = "reminded_twice"
    ANSWERED = "answered"
    MISSED = "missed"


def progress_is_unknown(text: str) -> bool:
    """Recognize only whole uncertainty replies, never keywords inside a scope."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"[\s，。！？,.!?、'’]", "", normalized)
    if normalized in {"idontknow", "imnotsure", "notsure", "unknown", "不知道", "不清楚", "不確定", "忘了"}:
        return True
    return re.fullmatch(
        r"(?:我)?(?:也)?(?:不知道|不清楚|不確定|忘記了|忘了)"
        r"(?:今天)?(?:上到哪裡|上到哪|上了什麼|上課範圍|進度)?(?:了)?", normalized
    ) is not None


def progress_followup(session: "ProgressSession", review_summary: str | None = None) -> tuple[str, str, str]:
    short_date = session.class_date.strftime("%m/%d")
    if progress_is_unknown(session.reported_progress or ""):
        return (f"確認{session.course_name} {short_date} 上課範圍",
                "確認範圍", "確認範圍後")
    detail = (review_summary or session.reported_progress or "").strip()
    return (f"複習{session.course_name} {short_date}：{detail}",
            "複習", "複習後")


@dataclass(frozen=True)
class CourseSlot:
    key: str
    weekday: int  # Monday=0, Sunday=6
    start: time
    end: time
    name: str
    prompt_time: time


@dataclass(frozen=True)
class ProgressSession:
    session_id: str
    course_key: str
    course_name: str
    class_date: date
    prompt_message_id: int
    status: ProgressStatus
    reminder_count: int
    reported_progress: str | None = None
    reply_message_id: int | None = None
    survey_task_id: int | None = None


COURSE_SCHEDULE: tuple[CourseSlot, ...] = (
    CourseSlot("security", 0, time(9, 20), time(12, 5), "資訊安全實務與管理", time(12, 10)),
    CourseSlot("computer-architecture", 1, time(13, 10), time(16, 0), "計算機結構", time(16, 5)),
    CourseSlot("software-engineering", 2, time(9, 20), time(12, 5), "軟體工程", time(12, 10)),
    CourseSlot("graph-algorithms", 2, time(13, 10), time(16, 0), "圖論演算法", time(16, 5)),
    CourseSlot("database-systems", 3, time(9, 20), time(12, 5), "資料庫系統", time(12, 10)),
    CourseSlot("competitive-programming", 3, time(18, 30), time(21, 10), "程式競賽技巧導論", time(21, 15)),
    CourseSlot("operating-systems", 4, time(9, 20), time(12, 5), "作業系統", time(12, 10)),
)


def course_for_weekday(weekday: int) -> tuple[CourseSlot, ...]:
    return tuple(slot for slot in COURSE_SCHEDULE if slot.weekday == weekday)


def prompt_text(course_name: str) -> str:
    return (f"{course_name}剛下課。請回覆這則訊息，告訴我今天上到哪裡。\n"
            "你可以使用章節、頁碼、講義名稱或自然語言描述。\n"
            "回覆後會完成填寫進度代辦，並新增複習代辦；複習完成後再自行勾選。\n"
            "若不知道進度，可直接回覆「不知道」，改建立確認上課範圍代辦。")


def new_session(slot: CourseSlot, class_date: date, prompt_message_id: int) -> ProgressSession:
    return ProgressSession(
        session_id=f"{slot.key}:{class_date.isoformat()}",
        course_key=slot.key,
        course_name=slot.name,
        class_date=class_date,
        prompt_message_id=prompt_message_id,
        status=ProgressStatus.PENDING,
        reminder_count=0,
    )


def accept_reply(session: ProgressSession, *, reply_to_message_id: int, reply_message_id: int, text: str) -> ProgressSession | None:
    """Accept only a Telegram reply to this session's prompt."""
    cleaned = text.strip()
    if reply_to_message_id != session.prompt_message_id or not cleaned:
        return None
    if session.status in {ProgressStatus.ANSWERED, ProgressStatus.MISSED}:
        return None
    return replace(
        session,
        status=ProgressStatus.ANSWERED,
        reported_progress=cleaned,
        reply_message_id=reply_message_id,
    )


def due_reminder(session: ProgressSession, *, now: datetime, prompt_sent_at: datetime) -> int | None:
    """Return reminder number 1/2 when due, otherwise None.

    A reminder is due one hour after the prompt and one more hour later.  No
    reminder is due once the session is answered/missed or after two reminders.
    """
    if session.status in {ProgressStatus.ANSWERED, ProgressStatus.MISSED}:
        return None
    elapsed = now - prompt_sent_at
    if now.utcoffset() is None or prompt_sent_at.utcoffset() is None:
        raise ValueError("reminder timestamps must be timezone-aware")
    if now.astimezone(TAIPEI).date() != session.class_date:
        return None
    if session.reminder_count < 1 and elapsed >= timedelta(hours=1):
        return 1
    if session.reminder_count < 2 and elapsed >= timedelta(hours=2):
        return 2
    return None


def record_reminder(session: ProgressSession, reminder_number: int) -> ProgressSession:
    if session.status in {ProgressStatus.ANSWERED, ProgressStatus.MISSED}:
        raise ValueError("terminal sessions cannot be reminded")
    if reminder_number not in {1, 2} or reminder_number != session.reminder_count + 1:
        raise ValueError("reminders must be recorded in order and stop at two")
    status = ProgressStatus.REMINDED_ONCE if reminder_number == 1 else ProgressStatus.REMINDED_TWICE
    return replace(session, status=status, reminder_count=reminder_number)


def mark_missed_at_day_end(session: ProgressSession, *, local_date: date) -> ProgressSession:
    # The scheduler may restart several days later: expire every older session.
    if session.class_date >= local_date or session.status in {ProgressStatus.ANSWERED, ProgressStatus.MISSED}:
        return session
    return replace(session, status=ProgressStatus.MISSED)

"""Minute-driven course prompts with durable send claims and restart recovery."""

from datetime import datetime, timedelta

from .course_tracking import (TAIPEI, ProgressStatus, course_for_weekday, new_session,
                             prompt_text, mark_missed_at_day_end, record_reminder)
from .study_delivery import StudyDeliveryLedger
from .telegram import TelegramError
from .academic_calendar import CalendarEvent, day_policy
from .calendar_snapshot import is_current


def delivery_outcome(result: object) -> tuple[int | None, bool]:
    """Treat malformed or server-error responses as uncertain, never retryable."""
    if not isinstance(result, dict):
        return None, False
    body = result.get("result")
    if result.get("ok") is True and isinstance(body, dict):
        message_id = body.get("message_id")
        if type(message_id) is int and message_id > 0:
            return message_id, False
    code = result.get("error_code")
    rejected = result.get("ok") is False and type(code) is int and code in {400, 401, 403}
    return None, rejected


async def tick_study(db, telegram, chat_id: int, now: datetime) -> dict:
    if now.utcoffset() is None:
        raise ValueError("scheduler clock must be timezone-aware")
    now = now.astimezone(TAIPEI)
    snapshot = db.get_calendar_snapshot()
    policy = (day_policy([CalendarEvent(**row) for row in snapshot["events"]], now.date())
              if is_current(snapshot, now) else "unverified")
    suppressed = policy in {"no_class", "exam_period"}
    ledger = StudyDeliveryLedger(db)
    created = 0
    for slot in course_for_weekday(now.weekday()):
        if suppressed:
            continue
        due = datetime.combine(now.date(), slot.prompt_time, tzinfo=TAIPEI)
        if now < due:
            continue
        candidate = new_session(slot, now.date(), 0)
        if db.get_course_session(candidate.session_id) is not None:
            continue
        key = candidate.session_id + ":prompt"
        claim = ledger.claim(key, now)
        if claim is not None:
            try:
                result = await telegram.send_message(chat_id, prompt_text(slot.name))
            except TelegramError:
                ledger.finish(key, claim, now)
                continue
            message_id, rejected = delivery_outcome(result)
            # Only a definitive client rejection is safe to retry. Server errors
            # and malformed responses may have followed a successful send.
            ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        delivery = db.get_study_delivery(key)
        if delivery and delivery["status"] == "sent":
            # A crash after recording the receipt is repaired without re-sending.
            db.create_course_session(new_session(slot, now.date(), delivery["message_id"]), create_tasks=True)
            created += 1
    reminders = 0
    for session in db.list_pending_course_sessions():
        session = db.mutate_course_session(session.session_id,
            lambda current: mark_missed_at_day_end(current, local_date=now.date()))
        if suppressed:
            continue
        if session.status in {ProgressStatus.ANSWERED, ProgressStatus.MISSED} or session.reminder_count >= 2:
            continue
        number = session.reminder_count + 1
        previous_key = session.session_id + (":prompt" if number == 1 else ":reminder:1")
        previous = db.get_study_delivery(previous_key)
        if not previous or previous["status"] != "sent":
            continue
        sent_at = datetime.fromisoformat(previous.get("sent_at", previous["claimed_at"]))
        if now < sent_at + timedelta(hours=1):
            continue
        key = f"{session.session_id}:reminder:{number}"
        claim = ledger.claim(key, now)
        if claim:
            # Recheck after acquiring the send claim. An in-flight API call cannot
            # be recalled, but its completion must never overwrite an answer.
            current = db.get_course_session(session.session_id)
            if current.status in {ProgressStatus.ANSWERED, ProgressStatus.MISSED}:
                continue
            try:
                result = await telegram.send_message(chat_id,
                    f"還沒收到{session.course_name}的課堂進度。請回覆原始課後訊息。",
                    reply_to_message_id=session.prompt_message_id)
            except TelegramError:
                ledger.finish(key, claim, now)
                continue
            message_id, rejected = delivery_outcome(result)
            ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        delivery = db.get_study_delivery(key)
        if delivery and delivery["status"] == "sent":
            def advance(current):
                if current.status in {ProgressStatus.ANSWERED, ProgressStatus.MISSED} or current.reminder_count >= number:
                    return current
                return record_reminder(current, number)
            db.mutate_course_session(session.session_id, advance)
            reminders += 1
    return {"sessions_reconciled": created, "reminders_reconciled": reminders,
            "calendar_policy": policy, "course_prompts_suppressed": suppressed}


async def notify_study_failures(db, telegram, chat_id: int, now: datetime) -> dict:
    """Bound notifications by source key; never recursively report notice failures."""
    ledger = StudyDeliveryLedger(db)
    sent = 0
    unresolved = 0
    for source_key in db.list_failed_study_deliveries():
        key = "notice:" + source_key
        claim = ledger.claim(key, now)
        if claim:
            try:
                result = await telegram.send_message(chat_id,
                    "一則課後通知無法確認送達，或已達重試上限。"
                    "Chronos 已停止自動重送該則訊息；請檢查今天的課後問題。")
            except TelegramError:
                ledger.finish(key, claim, now)
            else:
                message_id, rejected = delivery_outcome(result)
                state = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
                sent += state["status"] == "sent"
        state = db.get_study_delivery(key)
        unresolved += state["status"] != "sent"
    return {"failure_notices_sent": sent, "failure_notices_unresolved": unresolved}

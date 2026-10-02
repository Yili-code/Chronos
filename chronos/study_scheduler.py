"""Minute-driven course prompts with durable send claims and restart recovery."""

from datetime import datetime

from .course_tracking import TAIPEI, course_for_weekday, new_session, prompt_text
from .study_delivery import StudyDeliveryLedger
from .telegram import TelegramError


async def tick_study(db, telegram, chat_id: int, now: datetime) -> dict:
    if now.utcoffset() is None:
        raise ValueError("scheduler clock must be timezone-aware")
    now = now.astimezone(TAIPEI)
    ledger = StudyDeliveryLedger(db)
    created = 0
    for slot in course_for_weekday(now.weekday()):
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
            message_id = (result.get("result") or {}).get("message_id") if result.get("ok") is True else None
            # Only a definitive client rejection is safe to retry. Server errors
            # and malformed responses may have followed a successful send.
            rejected = result.get("ok") is False and result.get("error_code") in {400, 401, 403}
            ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        delivery = db.get_study_delivery(key)
        if delivery and delivery["status"] == "sent":
            # A crash after recording the receipt is repaired without re-sending.
            db.create_course_session(new_session(slot, now.date(), delivery["message_id"]))
            created += 1
    return {"sessions_reconciled": created}

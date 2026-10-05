"""Holiday notices from current, explicit official-calendar evidence only."""
from datetime import timedelta

from .academic_calendar import CalendarEvent, day_policy
from .calendar_snapshot import is_current
from .course_tracking import TAIPEI
from .study_delivery import StudyDeliveryLedger
from .study_scheduler import delivery_outcome
from .telegram import TelegramError


def holiday_notices(snapshot, now):
    """Catch up today's eligible windows, never replay past holiday dates."""
    if not is_current(snapshot, now):
        return []
    local = now.astimezone(TAIPEI)
    events = [CalendarEvent(**row) for row in snapshot["events"]]
    candidates = [(local.date(), "today", 8),
                  (local.date() + timedelta(days=1), "tomorrow", 12)]
    notices = []
    for day, window, hour in candidates:
        if local.hour < hour or day_policy(events, day) != "no_class":
            continue
        key = f"calendar:holiday:{day.isoformat()}:{window}"
        label = "今天" if window == "today" else "明天"
        # Do not imply course-prompt suppression has been activated here.
        text = (f"{label}（{day:%Y-%m-%d}）官方校曆列為放假／停止上課。\n"
                f"依據：{snapshot['source_url']}\n"
                f"校曆查核時間：{snapshot['fetched_at']}")
        notices.append((key, text))
    return notices


async def tick_calendar(db, telegram, chat_id, now):
    snapshot = db.get_calendar_snapshot()
    ledger = StudyDeliveryLedger(db)
    sent = 0
    for key, text in holiday_notices(snapshot, now):
        claim = ledger.claim(key, now)
        if claim is None:
            continue
        try:
            response = await telegram.send_message(chat_id, text)
        except TelegramError:
            ledger.finish(key, claim, now)
            continue
        message_id, rejected = delivery_outcome(response)
        result = ledger.finish(key, claim, now, message_id=message_id,
                               definitely_rejected=rejected)
        sent += result["status"] == "sent"
    return {"holiday_notices_sent": sent, "calendar_current": is_current(snapshot, now)}

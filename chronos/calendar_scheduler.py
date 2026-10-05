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


def calendar_advisories(snapshot, now, *, exhausted=False):
    local = now.astimezone(TAIPEI)
    if not is_current(snapshot, now):
        if not exhausted:
            return []
        return [(f"calendar:health:{local.date()}:unverified",
                 "今天的官方校曆更新已達嘗試上限，尚無當日有效資料。"
                 "Chronos 不會根據舊資料停發課後調查；請自行確認是否停課。")]
    events = [CalendarEvent(**row) for row in snapshot['events']]
    return [(f"calendar:health:{day}:ambiguous",
             f"{day} 的官方校曆含需確認或衝突的上課資訊。"
             "請向授課教師確認，再用 /classday 設定指定課程；尚未確認前照課表詢問。")
            for day in (local.date(), local.date() + timedelta(days=1))
            if day_policy(events, day) == 'needs_confirmation']


async def tick_calendar(db, telegram, chat_id, now, *, sync_result=None):
    snapshot = db.get_calendar_snapshot()
    ledger = StudyDeliveryLedger(db)
    sent = 0
    health_sent = 0
    notices = holiday_notices(snapshot, now)
    # A closure first observed before 08:00 still deserves immediate notice if
    # no previous-day notice was confirmed. The scheduled 08:00 reminder remains.
    local = now.astimezone(TAIPEI)
    if is_current(snapshot, now) and local.hour < 8:
        events = [CalendarEvent(**row) for row in snapshot['events']]
        prior = db.get_study_delivery(f"calendar:holiday:{local.date()}:tomorrow")
        if day_policy(events, local.date()) == 'no_class' and (not prior or prior['status'] != 'sent'):
            notices.append((f"calendar:holiday:{local.date()}:late",
                f"剛確認今天（{local.date()}）官方校曆列為放假／停止上課。\n"
                f"依據：{snapshot['source_url']}"))
    exhausted = (sync_result or {}).get('calendar_attempts', 0) >= 3
    notices += calendar_advisories(snapshot, now, exhausted=exhausted)
    for key, text in notices:
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
        if key.startswith('calendar:health:'):
            health_sent += result['status'] == 'sent'
        else:
            sent += result["status"] == "sent"
    return {"holiday_notices_sent": sent, "calendar_current": is_current(snapshot, now),
            "calendar_health_notices_sent": health_sent}

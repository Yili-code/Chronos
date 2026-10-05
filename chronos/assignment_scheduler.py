"""Durable assignment notices. No upstream fetching, AI or submission here."""
from .assignments import due_reminder
from .study_delivery import StudyDeliveryLedger
from .study_scheduler import delivery_outcome
from .telegram import TelegramError


async def tick_assignments(db, telegram, chat_id, now):
    ledger = StudyDeliveryLedger(db)
    sent = 0
    for source_key in db.list_assignment_keys():
        record = db.get_assignment(source_key)
        if not record or not record["task_exists"] or record["assignment"].status == "done":
            continue
        item, task_id = record["assignment"], record["task_id"]
        notice_key = f"assignment:{source_key}:discovered"
        notice = db.get_study_delivery(notice_key)
        initial = not notice or notice["status"] != "sent"
        key = notice_key if initial else due_reminder(item, now, sent_keys=set())
        if key is None:
            continue
        claim = ledger.claim(key, now)
        if claim is None:
            continue
        current = db.get_assignment(source_key)
        if not current or not current["task_exists"] or current["assignment"].status == "done":
            continue
        item = current["assignment"]
        if not initial and due_reminder(item, now, sent_keys=set()) != key:
            continue  # a concurrently changed deadline invalidates this claim
        deadline = (f"截止：{item.deadline:%Y-%m-%d %H:%M}（台北時間）" if item.deadline
                    else f"截止時間尚未提供。請用 /deadline {task_id} YYYY-MM-DD HH:MM 確認。")
        text = (f"{'新作業' if initial else '作業截止提醒'} #{task_id}：{item.title[:500]}\n"
                f"課程：{item.course_id[:100]}\n{deadline}\n"
                "完成後請在 /tasks 找到此作業，再用 /done 清單順位 結案。")
        try:
            response = await telegram.send_message(chat_id, text)
        except TelegramError:
            ledger.finish(key, claim, now)
            continue
        message_id, rejected = delivery_outcome(response)
        result = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        sent += result["status"] == "sent"
    return {"assignment_notices_sent": sent}

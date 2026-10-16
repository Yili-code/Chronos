"""Durable first-seen assignment notices. No upstream fetching, AI or submission here."""
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
        key = notice_key if initial else None
        if key is None:
            continue
        claim = ledger.claim(key, now)
        if claim is None:
            continue
        current = db.get_assignment(source_key)
        if not current or not current["task_exists"] or current["assignment"].status == "done":
            continue
        item = current["assignment"]
        deadline = (f"截止：{item.deadline:%m/%d %H:%M}（台北時間）" if item.deadline
                    else "截止時間尚未提供。請先用 /tasks 定位，再以 /edit 清單順位 ... 補上。")
        label = '新作業'
        text = (f"{label} #{task_id}：{item.title[:500]}\n"
                f"課程：{item.course_id[:100]}\n{deadline}\n"
                + ("TronClass 頁面顯示已繳交；是否結案仍由你確認。\n" if item.submission_status == 'submitted'
                   else "TronClass 繳交狀態尚未確認。\n") +
                f"已觀察到 PDF 附件：{len(item.attachments)} 個（僅部分清單，未代表已下載）。\n"
                f"說明：{item.description[:1000] or '尚未提供'}\n"
                "完成後，用 /tasks 定位，再以 /done 清單順位 結案。")
        try:
            markup = None
            if initial:
                markup = {"inline_keyboard": [[
                    {"text": "Open in TronClass", "url": f"https://tronclass.ntou.edu.tw/course/{item.course_id}/learning-activity#/{item.source_id}"},
                    {"text": "Edit task", "callback_data": f"task:edit:{task_id}"},
                ]]}
            response = await telegram.send_message(chat_id, text, reply_markup=markup)
        except TelegramError:
            ledger.finish(key, claim, now)
            continue
        message_id, rejected = delivery_outcome(response)
        result = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        sent += result["status"] == "sent"
    return {"assignment_notices_sent": sent}

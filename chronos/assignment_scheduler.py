"""Durable first-seen assignment notices. No upstream fetching, AI or submission here."""
from html import escape
from .assignment_bridge import TRACKED_COURSES
from .study_delivery import StudyDeliveryLedger
from .study_scheduler import delivery_outcome
from .telegram import TelegramError

COURSE_NAMES = {
    "189684": "Database Systems",
    "189687": "Computer Architecture",
    "189717": "Information Security Practice and Management",
    "192072": "Operating Systems",
    "189756": "Graph Algorithms",
    "188571": "Software Engineering",
    "193842": "Introduction to Competitive Programming",
}


async def tick_assignments(db, telegram, chat_id, now):
    ledger = StudyDeliveryLedger(db)
    sent = 0
    for source_key in db.list_assignment_keys():
        record = db.get_assignment(source_key)
        if not record or not record["task_exists"] or record["assignment"].status == "done":
            continue
        item, task_id = record["assignment"], record["task_id"]
        if item.course_id not in TRACKED_COURSES:
            continue
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
        if item.course_id not in TRACKED_COURSES:
            continue
        deadline = (f"{item.deadline:%m/%d %H:%M}" if item.deadline
                    else "Not provided. Use /tasks, then /edit to set a deadline.")
        label = 'New assignment'
        description = item.description.strip()
        text = (f"<b>Course</b> {escape(COURSE_NAMES.get(item.course_id, item.course_id))}\n"
                f"<b>{label}: {escape(item.title[:500])}</b>\n\n"
                f"<b>Due</b> {deadline}\n"
                f"<b>PDF attachments</b> {len(item.attachments)}\n"
                + (f"<b>Description</b> {escape(description[:1000])}\n"
                   if description and description != '尚未提供' else '') +
                ("TronClass shows submitted; mark complete when ready." if item.submission_status == 'submitted'
                 else "TronClass submission status unknown."))
        try:
            markup = None
            if initial:
                markup = {"inline_keyboard": [[
                    {"text": "Open in TronClass", "url": f"https://tronclass.ntou.edu.tw/course/{item.course_id}/learning-activity#/{item.source_id}"},
                    {"text": "Edit task", "callback_data": f"task:edit:{task_id}"},
                ]]}
            response = await telegram.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
        except TelegramError:
            ledger.finish(key, claim, now)
            continue
        message_id, rejected = delivery_outcome(response)
        result = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        sent += result["status"] == "sent"
    return {"assignment_notices_sent": sent}

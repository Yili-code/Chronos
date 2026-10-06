"""Deliver saved bulletin versions, never claiming stable upstream identity."""
from .announcement_commands import short_datetime
from .study_delivery import StudyDeliveryLedger
from .study_scheduler import delivery_outcome
from .telegram import TelegramError


async def tick_announcements(db, telegram, chat_id, now, limit=10):
    ledger = StudyDeliveryLedger(db)
    sent = 0
    attempted = 0
    for fingerprint, record in db.list_announcements():
        if attempted >= limit:
            break
        item = record['announcement']
        text = (f"課程公告內容版本：{item['title']}\n課程：{item['course_id']}\n"
                f"發布：{short_datetime(item['published_at'])}\n"
                '來源未提供穩定識別碼，可能是新公告或既有公告修改；連結已移除。\n\n'
                + item['content'])
        parts = [text[i:i+1500] for i in range(0, len(text), 1500)]
        for index, part in enumerate(parts):
            key = f'announcement:{fingerprint}:part:{index}'
            state = db.get_study_delivery(key)
            if state and state['status'] == 'sent':
                continue
            claim = ledger.claim(key, now)
            if claim is None:
                break
            attempted += 1
            try:
                response = await telegram.send_message(chat_id, f'{part}\n\n公告分段 {index+1}/{len(parts)}')
            except TelegramError:
                ledger.finish(key, claim, now)
                break
            message_id, rejected = delivery_outcome(response)
            state = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
            if state['status'] != 'sent':
                break
            sent += 1
            if attempted >= limit:
                break
    return {'announcement_parts_sent':sent}

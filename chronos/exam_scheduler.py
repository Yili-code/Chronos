"""Calendar-triggered exam notices containing only owner-provided facts."""
from datetime import date, datetime, timedelta

from .calendar_snapshot import is_current
from .course_tracking import TAIPEI
from .exams import exam_key, format_exams
from .study_delivery import StudyDeliveryLedger
from .study_scheduler import delivery_outcome
from .telegram import TelegramError


def exam_notices(snapshot, records, now):
    if not is_current(snapshot, now):
        return []
    local = now.astimezone(TAIPEI)
    today = local.date()
    periods = {(date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date']))
               for row in snapshot['events'] if row['classification'] == 'exam_period'}
    notices = []
    for start, end in sorted(periods):
        if start - timedelta(days=7) <= today < start:
            notices.append((f'exam:confirm:{start}:{end}',
                f'官方考試週為 {start} 至 {end}。請確認科目、日期時間、地點、範圍及待複習項目。\n'
                '用 /exam 保存資料；未知欄位填 ?。不會依考試週推測個別科目安排。'))
    if local.hour < 8 or not any(start <= today <= end for start, end in periods):
        return notices
    confirmed = [record for record in records if record['starts_at'] and
                 datetime.fromisoformat(record['starts_at']).astimezone(TAIPEI).date() == today]
    prefix = f'exam:daily:{today}'
    notices.append((prefix + ':header', f'{today} 考試週提醒\n' +
                    ('以下為已保存的當日考試與複習事項。' if confirmed else
                     '當日考試與複習安排：尚未提供。這不代表今天沒有考試。') +
                    '\n請用 /exam 補充，或 /exams 查閱紀錄。'))
    for record in sorted(confirmed, key=exam_key):
        text = format_exams([record])
        # Bound UTF-16 length too: 1,500 Unicode code points <= 3,000 units.
        for index, offset in enumerate(range(0, len(text), 1500)):
            notices.append((f'{prefix}:{exam_key(record)}:{index}', text[offset:offset + 1500]))
    return notices


async def tick_exams(db, telegram, chat_id, now):
    ledger = StudyDeliveryLedger(db)
    sent = 0
    for key, text in exam_notices(db.get_calendar_snapshot(), db.list_exams(), now):
        previous = db.get_study_delivery(key)
        if previous and previous['status'] == 'sent':
            continue
        claim = ledger.claim(key, now)
        if claim is None:
            break  # do not send later parts after an uncertain or pending part
        try:
            response = await telegram.send_message(chat_id, text)
        except TelegramError:
            ledger.finish(key, claim, now)
            break
        message_id, rejected = delivery_outcome(response)
        state = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        if state['status'] != 'sent':
            break
        sent += 1
    return {'exam_notices_sent': sent}

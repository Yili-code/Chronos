"""Owner-confirmed exam records; unknown fields never become inferred facts."""
from datetime import datetime
from hashlib import sha256
import re

from .course_tracking import TAIPEI


def validate_exam(record):
    fields = {'course', 'period', 'starts_at', 'location', 'scope', 'review'}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError('invalid exam record')
    for key in fields - {'starts_at'}:
        value = record[key]
        if value is None and key in {'location', 'scope', 'review'}:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 1000 or '\0' in value:
            raise ValueError('invalid exam field')
    if record['starts_at'] is not None:
        parsed = datetime.fromisoformat(record['starts_at'])
        if parsed.utcoffset() is None:
            raise ValueError('exam time requires timezone')
        record = {**record, 'starts_at': parsed.astimezone(TAIPEI).isoformat()}
    return dict(record)


def exam_key(record):
    return sha256((record['period'] + '\0' + record['course']).encode()).hexdigest()


def exam_action(db, command):
    usage = ('用法：/exam 科目 | 考試名稱（例如期中） | YYYY-MM-DD HH:MM | 地點 | 範圍 | 待複習項目\n'
             '時間以台北時間計；未知欄位填 ?。相同科目與考試名稱會更新原紀錄。')
    parts = [part.strip() for part in command.removeprefix('exam').split('|')]
    if len(parts) != 6:
        return lambda: usage
    course, period, time, location, scope, review = parts
    try:
        starts = None if time == '?' else datetime.strptime(time, '%Y-%m-%d %H:%M').replace(tzinfo=TAIPEI).isoformat()
        record = validate_exam({'course': course, 'period': period, 'starts_at': starts,
            'location': None if location == '?' else location,
            'scope': None if scope == '?' else scope,
            'review': None if review == '?' else review})
        if course == '?' or period == '?':
            raise ValueError('exam identity required')
    except ValueError:
        return lambda: usage
    def save():
        db.save_exam(record)
        return "已保存考試紀錄。用 /exams 查閱；未提供的資料保持未知，不會推測考試安排。"
    return save


def save_exam_action(db, record):
    """Persist an already parsed exam inside the Telegram update transaction."""
    try:
        record = validate_exam(record)
    except (TypeError, ValueError):
        return lambda: "無法確認考試資料，未保存。請補充科目與考試名稱後重試。"
    return lambda: _save_exam(db, record)


def _save_exam(db, record):
    db.save_exam(record)
    return "已保存考試紀錄。用 /exams 查閱；未提供的資料保持未知，不會推測考試安排。"


def format_exams(records):
    if not records:
        return '尚未提供考試資料。用 /exam 保存已確認的安排。'
    lines = []
    for record in records:
        when = (datetime.fromisoformat(record['starts_at']).strftime('%Y-%m-%d %H:%M（台北時間）')
                if record['starts_at'] else '尚未提供')
        lines.append(f"{record['course']}／{record['period']}\n時間：{when}\n"
                     f"地點：{record['location'] or '尚未提供'}\n範圍：{record['scope'] or '尚未提供'}\n"
                     f"待複習：{record['review'] or '尚未提供'}")
    return '\n\n'.join(lines)


def exam_pages(records):
    """Stable record order and lossless, conservatively bounded Unicode chunks."""
    text = format_exams(sorted(records, key=exam_key))
    return [text[offset:offset + 1500] for offset in range(0, len(text), 1500)]


def exams_query(db, command):
    match = re.fullmatch(r'exams(?:\s+([1-9][0-9]{0,5}))?', command)
    if not match:
        return '用法：/exams [頁碼]，例如 /exams 2。'
    page = int(match[1] or 1)
    pages = exam_pages(db.list_exams())
    if page > len(pages):
        return f'沒有第 {page} 頁，目前共 {len(pages)} 頁。請用 /exams 從第一頁查閱。'
    footer = f'\n\n第 {page}/{len(pages)} 頁'
    if page < len(pages):
        footer += f'；下一頁：/exams {page + 1}'
    return pages[page - 1] + footer

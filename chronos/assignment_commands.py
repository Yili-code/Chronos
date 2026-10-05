"""Explicit deadline input without AI date inference or a network request."""
from datetime import datetime
import re

from .course_tracking import TAIPEI


def assignment_text(record):
    item = record['assignment']
    deadline = item.deadline.strftime('%Y-%m-%d %H:%M（台北時間）') if item.deadline else '尚未提供'
    submission = '頁面曾顯示已繳交，未代表你已確認結案' if item.submission_status == 'submitted' else '尚未確認'
    attachments = '\n'.join(f'- {filename}（來源 ID {source}）' for source, filename in item.attachments) or '尚未觀察到；不代表沒有附件'
    completed = item.completed_at.strftime('%Y-%m-%d %H:%M') if item.completed_at else '尚未完成'
    return (f"作業 #{record['task_id']}：{item.title}\n課程：{item.course_id}\n"
            f"截止：{deadline}\n完成紀錄：{completed}\nTronClass 繳交證據：{submission}\n"
            f"來源內容版本：{item.source_revision}\n\n作業說明\n{item.description or '尚未提供'}\n\n"
            f"PDF 附件（部分觀察清單，未代表已下載）\n{attachments}")


def assignment_query(db, command):
    match = re.fullmatch(r'assignment\s+([1-9][0-9]{0,18})(?:\s+([1-9][0-9]{0,5}))?', command)
    if not match:
        return '用法：/assignment 作業固定ID [頁碼]。編號取自作業通知，不是 /tasks 順位。'
    record = next((record for key in db.list_assignment_keys()
                   if (record := db.get_assignment(key)) and record['task_id'] == int(match[1])), None)
    if record is None:
        return '找不到此作業紀錄。'
    text = assignment_text(record)
    pages = [text[offset:offset + 1500] for offset in range(0, len(text), 1500)]
    page = int(match[2] or 1)
    if page > len(pages):
        return f'目前共 {len(pages)} 頁。'
    footer = f'\n\n第 {page}/{len(pages)} 頁'
    if page < len(pages):
        footer += f'；下一頁：/assignment {match[1]} {page + 1}'
    return pages[page - 1] + footer


def deadline_action(db, command: str):
    match = re.fullmatch(r"deadline\s+([1-9]\d*)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", command)
    usage = "用法：/deadline 作業固定ID YYYY-MM-DD HH:MM（台北時間；不是 /tasks 的清單順位）。"
    if match is None:
        return lambda: usage
    try:
        deadline = datetime.strptime(f"{match[2]} {match[3]}", "%Y-%m-%d %H:%M").replace(tzinfo=TAIPEI)
    except ValueError:
        return lambda: usage
    def confirm():
        if not db.confirm_assignment_deadline(int(match[1]), deadline):
            return "找不到仍待完成的作業，未變更截止時間。"
        return f"作業 #{match[1]} 截止時間已確認：{deadline:%Y-%m-%d %H:%M}（台北時間）。"
    return confirm
